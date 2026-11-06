package com.authgebra.client;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BiFunction;

/** Local fakes: a scriptable HTTP server and a socket-level fault server. */
final class TestServers {
    private TestServers() {}

    static String userData(String id) {
        return "{\"id\":\"" + id + "\",\"username\":\"alice\",\"display_name\":\"Alice\","
                + "\"status\":\"active\",\"auth_version\":1,\"version\":1,\"etag\":\"W/\\\"1\\\"\","
                + "\"created_at_rfc3339\":\"2026-11-06T00:00:00Z\",\"mfa_enabled\":false,"
                + "\"roles_count\":0,\"sessions_count\":0}";
    }

    record Captured(String method, String path, String query, Map<String, String> headers, byte[] body) {
        String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }
    }

    record Reply(int status, Map<String, String> headers, byte[] body) {
        static Reply json(int status, String body) {
            return new Reply(status, Map.of("Content-Type", "application/json"), body.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
    }

    /** HTTP server: {@code script} receives (attempt number starting at 1, captured request). */
    static final class Http implements AutoCloseable {
        private final HttpServer server;
        final List<Captured> requests = Collections.synchronizedList(new ArrayList<>());
        private final AtomicInteger hits = new AtomicInteger();

        Http(BiFunction<Integer, Captured, Reply> script) throws IOException {
            server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
            server.createContext("/", exchange -> {
                byte[] body;
                try (InputStream in = exchange.getRequestBody()) {
                    body = in.readAllBytes();
                }
                Map<String, String> headers = new LinkedHashMap<>();
                exchange.getRequestHeaders().forEach((k, v) -> headers.put(k.toLowerCase(Locale.ROOT), v.get(0)));
                Captured captured = new Captured(exchange.getRequestMethod(), exchange.getRequestURI().getRawPath(),
                        exchange.getRequestURI().getRawQuery(), headers, body);
                requests.add(captured);
                Reply reply = script.apply(hits.incrementAndGet(), captured);
                reply.headers().forEach((k, v) -> exchange.getResponseHeaders().add(k, v));
                exchange.sendResponseHeaders(reply.status(), reply.body().length == 0 ? -1 : reply.body().length);
                if (reply.body().length > 0) {
                    exchange.getResponseBody().write(reply.body());
                }
                exchange.close();
            });
            server.start();
        }

        String baseUrl() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        int hits() {
            return hits.get();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    /** Accepts TCP connections and either never answers (timeout) or resets them. */
    static final class Fault implements AutoCloseable {
        enum Mode { HANG, RESET }

        private final ServerSocket socket;
        private final AtomicInteger connections = new AtomicInteger();
        private final List<Socket> held = Collections.synchronizedList(new ArrayList<>());
        private volatile boolean open = true;

        Fault(Mode mode) throws IOException {
            socket = new ServerSocket(0, 50, InetAddress.getLoopbackAddress());
            Thread thread = new Thread(() -> {
                while (open) {
                    try {
                        Socket client = socket.accept();
                        connections.incrementAndGet();
                        if (mode == Mode.RESET) {
                            client.getInputStream().read(new byte[1]);
                            client.setSoLinger(true, 0);
                            client.close();
                        } else {
                            held.add(client);
                        }
                    } catch (IOException e) {
                        return;
                    }
                }
            }, "fault-server");
            thread.setDaemon(true);
            thread.start();
        }

        String baseUrl() {
            return "http://127.0.0.1:" + socket.getLocalPort();
        }

        int connections() {
            return connections.get();
        }

        @Override
        public void close() throws IOException {
            open = false;
            socket.close();
            for (Socket s : held) {
                s.close();
            }
        }
    }
}
