package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import javax.tools.JavaCompiler;
import javax.tools.ToolProvider;
import org.junit.jupiter.api.Test;

/** Examples are documentation that must keep compiling against the public API. */
class ExamplesCompileTest {
    @Test
    void examplesCompile() throws Exception {
        List<String> args = new ArrayList<>(List.of("-d", Files.createTempDirectory("ex").toString(),
                "--release", "17", "-cp", System.getProperty("java.class.path")));
        try (Stream<Path> files = Files.list(Path.of("examples"))) {
            files.filter(p -> p.toString().endsWith(".java")).sorted().forEach(p -> args.add(p.toString()));
        }
        assertTrue(args.size() > 6, "examples directory must not be empty");
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        int rc = compiler.run(null, null, err, args.toArray(String[]::new));
        assertEquals(0, rc, err.toString());
    }
}
