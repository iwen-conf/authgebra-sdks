package com.authgebra.client;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JavaType;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.io.IOException;
import java.io.InputStream;
import java.net.ConnectException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpConnectTimeoutException;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.net.ssl.SSLException;

/** Request execution: auth, ids, retries, and turning responses into results or typed errors. */
final class Http {
    /** How a successful response body is shaped. */
    enum Kind { ENVELOPE, PAGED, BARE, VOID }

    /** Static description of one operation (generated). {@code queryParams}/{@code headerParams} null = unchecked. */
    record Operation(String operationId, String method, String path, Set<String> queryParams,
            Set<String> headerParams, Kind kind, boolean readOnly) {}

    private record RawResponse(int status, HttpHeaders headers, byte[] body) {}

    private record Envelope(int status, Map<String, List<String>> headers, JsonNode root, String requestId, String traceId) {}

    private static final ScheduledExecutorService WATCHDOG = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "authgebra-body-deadline");
        t.setDaemon(true);
        return t;
    });

    private final Config config;
    private final HttpClient client;
    private final boolean ownsClient;
    private final ObjectMapper mapper = JsonMapper.builder()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(DeserializationFeature.ACCEPT_FLOAT_AS_INT)
            .disable(MapperFeature.ALLOW_COERCION_OF_SCALARS)
            .serializationInclusion(JsonInclude.Include.NON_NULL)
            .build();

    Http(Config config) {
        this.config = config;
        this.ownsClient = config.httpClient() == null;
        this.client = ownsClient
                ? HttpClient.newBuilder().connectTimeout(config.connectTimeout())
                        .followRedirects(HttpClient.Redirect.NEVER).version(HttpClient.Version.HTTP_1_1).build()
                : config.httpClient();
    }

    ObjectMapper mapper() {
        return mapper;
    }

    /** Release resources of an SDK-created client (JDK 21+ {@code HttpClient.close()}; no-op on 17). */
    void close() {
        if (ownsClient) {
            try {
                HttpClient.class.getMethod("close").invoke(client);
            } catch (ReflectiveOperationException | RuntimeException ignored) {
                // JDK 17: connections are reclaimed by the client's idle timeout.
            }
        }
    }

    // ------------------------------------------------------------ typed calls

    <T> Result<T> call(Operation op, Object[] pathArgs, Query query, Object body, CallOptions options,
            TypeReference<T> type) {
        Envelope env = perform(op, pathArgs, query, body, options);
        JsonNode node = op.kind() == Kind.BARE ? env.root() : env.root().get("data");
        T data = convert(node, mapper.constructType(type.getType()), env);
        return result(env, data);
    }

    <T> Result<PagedData<T>> callPaged(Operation op, Object[] pathArgs, Query query, Object body,
            CallOptions options, TypeReference<T> itemType) {
        Envelope env = perform(op, pathArgs, query, body, options);
        JsonNode data = env.root().get("data");
        JsonNode page = env.root().get("page");
        if (data == null || !data.isArray() || page == null || !page.isObject()
                || page.get("has_more") == null || !page.get("has_more").isBoolean()) {
            throw malformedSuccess(env.status(), env.requestId(), null);
        }
        JavaType item = mapper.constructType(itemType.getType());
        List<T> items = new ArrayList<>(data.size());
        for (JsonNode element : data) {
            items.add(convert(element, item, env));
        }
        JsonNode cursor = page.get("next_cursor");
        String next = cursor == null || cursor.isNull() ? null : cursor.asText();
        return result(env, new PagedData<>(List.copyOf(items), next, page.get("has_more").asBoolean()));
    }

    Result<Void> callVoid(Operation op, Object[] pathArgs, Query query, Object body, CallOptions options) {
        return result(perform(op, pathArgs, query, body, options), null);
    }

    Result<JsonNode> raw(String method, String path, Query query, Object body, CallOptions options) {
        if (!path.startsWith("/") || path.startsWith("//") || path.contains("?") || path.contains("#")
                || path.chars().anyMatch(c -> c <= 0x20 || c == 0x7f)) {
            throw new IllegalArgumentException("path must be an absolute path without query, fragment or spaces");
        }
        String upper = method.toUpperCase(Locale.ROOT);
        boolean read = upper.equals("GET") || upper.equals("HEAD");
        Operation op = new Operation("raw", upper, path, null, null, Kind.ENVELOPE, read);
        Envelope env = perform(op, new Object[0], query, body, options);
        return result(env, env.root().get("data"));
    }

    private <T> T convert(JsonNode node, JavaType type, Envelope env) {
        if (node == null) {
            throw malformedSuccess(env.status(), env.requestId(), null);
        }
        try {
            return mapper.readerFor(type).readValue(node);
        } catch (IOException e) {
            throw malformedSuccess(env.status(), env.requestId(), e);
        }
    }

    private <T> Result<T> result(Envelope env, T data) {
        Models.Meta meta = null;
        JsonNode metaNode = env.root() == null ? null : env.root().get("meta");
        if (metaNode != null && metaNode.isObject()) {
            try {
                meta = mapper.treeToValue(metaNode, Models.Meta.class);
            } catch (JsonProcessingException e) {
                throw malformedSuccess(env.status(), env.requestId(), e);
            }
        }
        return new Result<>(env.status(), data, meta, env.requestId(), env.traceId(), false, env.headers());
    }

    private static ProtocolException malformedSuccess(int status, String requestId, Throwable cause) {
        return new ProtocolException(ProtocolException.MALFORMED_SUCCESS_BODY, status, requestId, cause);
    }

    // --------------------------------------------------------- request cycle

    private Envelope perform(Operation op, Object[] pathArgs, Query query, Object body, CallOptions options) {
        validate(op, query, options);
        String url = url(op, pathArgs, query);
        byte[] bodyBytes = null;
        if (body != null) {
            try {
                // Via String, not writeValueAsBytes: the byte generator escapes supplementary characters
                // (emoji) as \\uD83D\\uDE00; the contract says UTF-8 as-is.
                bodyBytes = mapper.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
            } catch (JsonProcessingException e) {
                throw new IllegalArgumentException("request body cannot be serialized: " + e.getOriginalMessage(), e);
            }
        }
        RetryPolicy policy = options.retryPolicy() != null ? options.retryPolicy() : config.retryPolicy();
        boolean readOnly = options.readOnly() != null ? options.readOnly() : op.readOnly();
        boolean writeRetryAllowed = !readOnly && policy.retryIdempotentWrites() && options.idempotencyKey() != null;
        String requestId = options.requestId() != null ? options.requestId() : config.requestIdSupplier().get();
        if (requestId == null || !CallOptions.REQUEST_ID.matcher(requestId).matches()) {
            requestId = "authgebra-java-" + java.util.UUID.randomUUID();
        }
        boolean explicitTrace = options.traceparent() != null;
        TraceContext trace = explicitTrace ? TraceContext.parse(options.traceparent()) : config.traceContextSupplier().get();

        for (int attempt = 1;; attempt++) {
            try {
                TraceContext attemptTrace = explicitTrace || attempt == 1 ? trace : trace.newSpan();
                RawResponse raw = send(op, url, bodyBytes, requestId, attemptTrace, options, readOnly, attempt);
                Envelope env = interpret(raw, op.kind());
                config.logger().log("authgebra op=" + op.operationId() + " status=" + raw.status()
                        + " request_id=" + requestId + " attempt=" + attempt);
                return env;
            } catch (AuthgebraException e) {
                boolean eligible = (readOnly || writeRetryAllowed) && attempt - 1 < policy.maxRetries()
                        && retryable(e, readOnly);
                if (!eligible) {
                    config.logger().log("authgebra op=" + op.operationId() + " failed request_id=" + requestId
                            + " attempt=" + attempt + " error=" + e.getClass().getSimpleName());
                    throw e;
                }
                Duration delay = delay(policy, attempt - 1, e);
                config.logger().log("authgebra op=" + op.operationId() + " retry request_id=" + requestId
                        + " next_attempt=" + (attempt + 1) + " delay_ms=" + delay.toMillis());
                try {
                    policy.sleeper().sleep(delay);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw new TransportException(TransportException.Kind.CANCELLED,
                            readOnly ? null : CommitState.UNKNOWN, attempt, interrupted);
                }
            }
        }
    }

    private static boolean retryable(AuthgebraException e, boolean readOnly) {
        if (e instanceof ApiException api) {
            String code = api.code();
            if (ErrorCodes.NOT_READY.equals(code)) {
                return true;
            }
            return readOnly ? ErrorCodes.RATE_LIMITED.equals(code) : ErrorCodes.IDEMPOTENCY_IN_PROGRESS.equals(code);
        }
        if (e instanceof TransportException transport) {
            return switch (transport.kind()) {
                case TIMEOUT, CONNECTION_RESET, CONNECT_FAILED -> true;
                case TLS, CANCELLED -> false;
            };
        }
        return false;
    }

    private static Duration delay(RetryPolicy policy, int retriesSoFar, AuthgebraException e) {
        if (e instanceof ApiException api && api.retryAfterSeconds() != null) {
            Duration asked = Duration.ofSeconds(api.retryAfterSeconds());
            return asked.compareTo(policy.maxRetryAfter()) > 0 ? policy.maxRetryAfter() : asked;
        }
        long ceiling = Math.min(policy.maxDelay().toMillis(), policy.baseDelay().toMillis() << Math.min(retriesSoFar, 20));
        return Duration.ofMillis(ThreadLocalRandom.current().nextLong(Math.max(1, ceiling) + 1));
    }

    private void validate(Operation op, Query query, CallOptions options) {
        if (query != null && op.queryParams() != null) {
            for (String name : query.values().keySet()) {
                if (!op.queryParams().contains(name)) {
                    throw new IllegalArgumentException(
                            "operation " + op.operationId() + " has no query parameter '" + name + "'; allowed: "
                                    + op.queryParams());
                }
            }
        }
        if (op.headerParams() != null) {
            if (options.idempotencyKey() != null && !op.headerParams().contains("Idempotency-Key")) {
                throw new IllegalArgumentException("operation " + op.operationId() + " does not accept Idempotency-Key");
            }
            if (options.ifMatch() != null && !op.headerParams().contains("If-Match")) {
                throw new IllegalArgumentException("operation " + op.operationId() + " does not accept If-Match");
            }
        }
        for (String name : options.headers().keySet()) {
            if (Config.isReservedHeader(name)) {
                throw new IllegalArgumentException("header is managed by the SDK: " + name);
            }
        }
    }

    private String url(Operation op, Object[] pathArgs, Query query) {
        StringBuilder path = new StringBuilder();
        int arg = 0;
        for (String segment : op.path().split("/")) {
            if (segment.isEmpty()) {
                continue;
            }
            path.append('/');
            if (segment.startsWith("{") && segment.endsWith("}")) {
                if (arg >= pathArgs.length || pathArgs[arg] == null) {
                    throw new IllegalArgumentException("missing path parameter " + segment);
                }
                path.append(encode(pathArgs[arg++].toString()));
            } else {
                path.append(segment);
            }
        }
        if (path.isEmpty()) {
            path.append('/');
        }
        String base = config.baseUrl().toString();
        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        String full = base + path;
        if (query != null && !query.values().isEmpty()) {
            StringBuilder qs = new StringBuilder();
            for (var entry : query.values().entrySet()) {
                qs.append(qs.isEmpty() ? '?' : '&').append(encode(entry.getKey())).append('=').append(encode(entry.getValue()));
            }
            full += qs;
        }
        return full;
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private RawResponse send(Operation op, String url, byte[] body, String requestId, TraceContext trace,
            CallOptions options, boolean readOnly, int attempt) {
        Duration timeout = options.requestTimeout() != null ? options.requestTimeout() : config.requestTimeout();
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url)).timeout(timeout)
                .header("Accept", "application/json")
                .header("User-Agent", config.userAgent())
                .header("X-Request-ID", requestId)
                .header("traceparent", trace.toTraceparent());
        if (options.tracestate() != null) {
            builder.header("tracestate", options.tracestate());
        }
        String token = config.tokenProvider().token();
        if (token != null && !token.isEmpty()) {
            if (token.indexOf('\r') >= 0 || token.indexOf('\n') >= 0) {
                throw new IllegalArgumentException("access token contains line breaks");
            }
            builder.header("Authorization", "Bearer " + token);
        }
        config.headers().forEach(builder::header);
        options.headers().forEach(builder::header);
        if (options.idempotencyKey() != null) {
            builder.header("Idempotency-Key", options.idempotencyKey());
        }
        if (options.ifMatch() != null) {
            builder.header("If-Match", options.ifMatch());
        }
        if (body != null) {
            builder.header("Content-Type", "application/json");
            builder.method(op.method(), HttpRequest.BodyPublishers.ofByteArray(body));
        } else {
            builder.method(op.method(), HttpRequest.BodyPublishers.noBody());
        }
        config.logger().log("authgebra op=" + op.operationId() + " " + op.method() + " " + op.path()
                + " request_id=" + requestId + " attempt=" + attempt);

        long deadline = System.nanoTime() + timeout.toNanos();
        String unknownIfWrite = readOnly ? null : CommitState.UNKNOWN;
        try {
            HttpResponse<InputStream> response = client.send(builder.build(), HttpResponse.BodyHandlers.ofInputStream());
            byte[] data = readBounded(response.body(), deadline, attempt, unknownIfWrite, response.statusCode());
            return new RawResponse(response.statusCode(), response.headers(), data);
        } catch (HttpConnectTimeoutException e) {
            throw new TransportException(TransportException.Kind.TIMEOUT, null, attempt, e);
        } catch (HttpTimeoutException e) {
            throw new TransportException(TransportException.Kind.TIMEOUT, unknownIfWrite, attempt, e);
        } catch (IOException e) {
            throw classify(e, unknownIfWrite, attempt);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new TransportException(TransportException.Kind.CANCELLED, unknownIfWrite, attempt, e);
        }
    }

    private static TransportException classify(IOException e, String unknownIfWrite, int attempt) {
        for (Throwable t = e; t != null; t = t.getCause()) {
            if (t instanceof SSLException) {
                return new TransportException(TransportException.Kind.TLS, null, attempt, e);
            }
            if (t instanceof HttpConnectTimeoutException) {
                return new TransportException(TransportException.Kind.TIMEOUT, null, attempt, e);
            }
            if (t instanceof HttpTimeoutException) {
                return new TransportException(TransportException.Kind.TIMEOUT, unknownIfWrite, attempt, e);
            }
            if (t instanceof ConnectException) {
                return new TransportException(TransportException.Kind.CONNECT_FAILED, null, attempt, e);
            }
        }
        return new TransportException(TransportException.Kind.CONNECTION_RESET, unknownIfWrite, attempt, e);
    }

    /** Reads at most {@code limit} bytes before {@code deadline}; always closes the stream. */
    private byte[] readBounded(InputStream in, long deadline, int attempt, String unknownIfWrite, int status)
            throws IOException {
        AtomicBoolean timedOut = new AtomicBoolean();
        long remaining = Math.max(1, deadline - System.nanoTime());
        ScheduledFuture<?> guard = WATCHDOG.schedule(() -> {
            timedOut.set(true);
            try {
                in.close();
            } catch (IOException ignored) {
                // closing is best effort; the read below fails and is classified
            }
        }, remaining, TimeUnit.NANOSECONDS);
        int limit = config.maxResponseBytes();
        try (in) {
            byte[] data = in.readNBytes(limit + 1);
            if (data.length > limit) {
                throw new ProtocolException(ProtocolException.RESPONSE_TOO_LARGE, status, null, null);
            }
            return data;
        } catch (IOException e) {
            if (timedOut.get()) {
                throw new TransportException(TransportException.Kind.TIMEOUT, unknownIfWrite, attempt, e);
            }
            throw e;
        } finally {
            guard.cancel(false);
        }
    }

    // ---------------------------------------------------------- interpretation

    private Envelope interpret(RawResponse raw, Kind kind) {
        Map<String, List<String>> headers = lowerCase(raw.headers());
        String headerRid = validRequestId(first(headers, "x-request-id"));
        String headerTrace = headerTraceId(headers);
        boolean success = raw.status() >= 200 && raw.status() < 300;
        if (success && kind == Kind.VOID) {
            return new Envelope(raw.status(), headers, parseLenient(raw.body()), headerRid, headerTrace);
        }
        JsonNode root = parse(raw);
        if (!success) {
            throw apiError(raw, headers, root, headerRid, headerTrace);
        }
        boolean ok = switch (kind) {
            case ENVELOPE -> root.isObject() && root.has("data");
            case PAGED -> root.isObject() && root.has("data") && root.has("page");
            case BARE -> root.isObject();
            case VOID -> true;
        };
        if (!ok) {
            throw new ProtocolException(ProtocolException.MALFORMED_SUCCESS_BODY, raw.status(), headerRid, null);
        }
        return new Envelope(raw.status(), headers, root, headerRid, headerTrace);
    }

    private JsonNode parse(RawResponse raw) {
        try {
            JsonNode node = mapper.readTree(raw.body());
            if (node == null || node.isMissingNode()) {
                throw new ProtocolException(ProtocolException.NON_JSON_RESPONSE, raw.status(), null, null);
            }
            return node;
        } catch (IOException e) {
            throw new ProtocolException(ProtocolException.NON_JSON_RESPONSE, raw.status(),
                    validRequestId(first(lowerCase(raw.headers()), "x-request-id")), e);
        }
    }

    private JsonNode parseLenient(byte[] body) {
        try {
            return body.length == 0 ? null : mapper.readTree(body);
        } catch (IOException e) {
            return null;
        }
    }

    private ApiException apiError(RawResponse raw, Map<String, List<String>> headers, JsonNode root,
            String headerRid, String headerTrace) {
        JsonNode error = root.get("error");
        if (error == null || !error.isObject() || !error.has("code") || !error.get("code").isTextual()
                || error.get("code").asText().isEmpty()) {
            throw new ProtocolException(ProtocolException.MALFORMED_ERROR_BODY, raw.status(), headerRid, null);
        }
        ApiException.Builder b = ApiException.builder();
        b.status = raw.status();
        b.code = error.get("code").asText();
        b.serverMessage = text(error, "message") == null ? "" : text(error, "message");
        String bodyRid = validRequestId(text(error, "request_id"));
        b.requestId = headerRid != null ? headerRid : bodyRid;
        b.requestIdMismatch = headerRid != null && bodyRid != null && !headerRid.equals(bodyRid);
        b.bodyRequestId = b.requestIdMismatch ? bodyRid : null;
        String bodyTrace = text(error, "trace_id");
        b.traceId = TraceContext.isValidTraceId(bodyTrace) ? bodyTrace : headerTrace;
        JsonNode details = error.get("details");
        b.details = details == null || details.isNull() ? null : details;
        b.violations = violations(details);
        JsonNode retryable = error.get("retryable");
        b.retryable = retryable != null && retryable.isBoolean() ? retryable.asBoolean() : null;
        JsonNode after = error.get("retry_after_seconds");
        if (after != null && after.canConvertToLong() && after.asLong() >= 0) {
            b.retryAfterSeconds = after.asLong();
        } else {
            b.retryAfterSeconds = retryAfterHeader(first(headers, "retry-after"));
        }
        b.commitState = text(error, "commit_state");
        b.responseSnippet = new String(raw.body(), 0, Math.min(raw.body().length, 1024), StandardCharsets.UTF_8);
        return b.build();
    }

    private static List<Violation> violations(JsonNode details) {
        List<Violation> out = new ArrayList<>();
        JsonNode list = details == null ? null : details.get("violations");
        if (list != null && list.isArray()) {
            for (JsonNode item : list) {
                if (item.isObject()) {
                    String reason = text(item, "reason");
                    out.add(new Violation(text(item, "path"), reason == null ? "" : reason,
                            text(item, "message") == null ? "" : text(item, "message")));
                }
            }
        }
        return out;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && value.isTextual() ? value.asText() : null;
    }

    private static Long retryAfterHeader(String value) {
        if (value == null || !value.matches("\\d{1,9}")) {
            return null;
        }
        return Long.parseLong(value);
    }

    private static String validRequestId(String value) {
        return value != null && CallOptions.REQUEST_ID.matcher(value).matches() ? value : null;
    }

    private static String headerTraceId(Map<String, List<String>> headers) {
        String direct = first(headers, "x-trace-id");
        if (TraceContext.isValidTraceId(direct)) {
            return direct;
        }
        TraceContext parsed = TraceContext.parse(first(headers, "traceparent"));
        return parsed == null ? null : parsed.traceId();
    }

    private static String first(Map<String, List<String>> headers, String name) {
        List<String> values = headers.get(name);
        return values == null || values.isEmpty() ? null : values.get(0);
    }

    private static Map<String, List<String>> lowerCase(HttpHeaders headers) {
        Map<String, List<String>> out = new LinkedHashMap<>();
        headers.map().forEach((k, v) -> out.put(k.toLowerCase(Locale.ROOT), List.copyOf(v)));
        return Map.copyOf(out);
    }
}
