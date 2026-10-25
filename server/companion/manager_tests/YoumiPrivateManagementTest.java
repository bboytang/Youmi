package xiaozhi.modules.device;

import static org.junit.jupiter.api.Assertions.*;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import xiaozhi.common.constant.Constant;
import xiaozhi.modules.security.service.SysUserTokenService;
import xiaozhi.modules.sys.service.SysParamsService;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {"server.servlet.context-path=/youmi", "mia.private-auth.allow-insecure-test-http=true"})
@ActiveProfiles("dev")
class YoumiPrivateManagementTest {
    private static final long OWNER = 940000001L, OTHER = 940000002L;
    private static final String AGENT = "mia-management-ci";
    private static final AtomicInteger calls = new AtomicInteger();
    private static final AtomicReference<JsonNode> forwarded = new AtomicReference<>();
    private static final HttpServer bridge = startBridge();
    @Autowired private TestRestTemplate http;
    @Autowired private JdbcTemplate db;
    @Autowired private SysUserTokenService tokens;
    @Autowired private SysParamsService params;
    @Autowired private ObjectMapper mapper;

    private static HttpServer startBridge() {
        try {
            var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/mia/management", exchange -> {
                calls.incrementAndGet();
                forwarded.set(new ObjectMapper().readTree(exchange.getRequestBody()));
                String auth = exchange.getRequestHeaders().getFirst("Authorization");
                byte[] body = (auth != null && auth.startsWith("Bearer ")
                    ? "{\"code\":0,\"data\":{\"features\":{\"memory_recall\":true},\"paused\":false,\"counts\":{\"memories\":2}}}"
                    : "{\"code\":1}").getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
                exchange.close();
            });
            server.start();
            return server;
        } catch (IOException error) { throw new ExceptionInInitializerError(error); }
    }

    @DynamicPropertySource
    static void managementUrl(DynamicPropertyRegistry registry) {
        registry.add("mia.companion.management-url", () -> "http://127.0.0.1:" + bridge.getAddress().getPort() + "/mia/management");
    }

    @AfterAll static void stopBridge() { bridge.stop(0); }

    @BeforeEach void fixtures() {
        calls.set(0); forwarded.set(null);
        db.update("DELETE FROM ai_agent WHERE id=?", AGENT);
        db.update("DELETE FROM sys_user_token WHERE user_id IN (?,?)", OWNER, OTHER);
        db.update("DELETE FROM sys_user WHERE id IN (?,?)", OWNER, OTHER);
        for (long user : new long[]{OWNER, OTHER}) {
            db.update("INSERT INTO sys_user(id,username,super_admin,status) VALUES (?,?,0,1)", user, "mia-settings-" + user);
        }
        db.update("INSERT INTO ai_agent(id,user_id,agent_name,system_prompt) VALUES (?,?,?,?)", AGENT, OWNER, "User character", "");
    }

    private JsonNode post(String path, Object body, String token) throws Exception {
        var headers = new HttpHeaders(); headers.setContentType(MediaType.APPLICATION_JSON);
        if (token != null) headers.setBearerAuth(token);
        return mapper.readTree(http.postForEntity(path, new HttpEntity<>(body, headers), String.class).getBody());
    }

    private String token(long user) { return tokens.createToken(user).getData().getToken(); }

    @Test void ownerUsesExistingLoginAndCannotForgeTheOperatorOrEditRole() throws Exception {
        JsonNode result = post("/agent/" + AGENT + "/mia-settings",
            Map.of("action", "status", "operator_id", OTHER, "owner", "forged"), token(OWNER));
        assertEquals(0, result.path("code").asInt());
        assertEquals(OWNER, forwarded.get().path("operator_id").asLong());
        assertEquals(AGENT, forwarded.get().path("agent_id").asText());
        assertEquals("", db.queryForObject("SELECT system_prompt FROM ai_agent WHERE id=?", String.class, AGENT));
        assertEquals(1, calls.get());
    }

    @Test void foreignUserAndAnonymousCallerNeverReachTheMemoryService() throws Exception {
        assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", Map.of("action", "status"), token(OTHER)).path("code").asInt());
        assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", Map.of("action", "status"), null).path("code").asInt());
        assertEquals(0, calls.get());
    }

    @Test void emotionalSystemUsesAgentOwnerAndDoesNotTrustBrowserIdentity() throws Exception {
        assertNotEquals(0, post("/agent/" + AGENT + "/affect-system",
            Map.of("action", "status"), token(OTHER)).path("code").asInt());
        assertEquals(0, calls.get());
        assertEquals(0, post("/agent/" + AGENT + "/affect-system",
            Map.of("action", "status", "owner", "forged"), token(OWNER)).path("code").asInt());
        assertEquals(OWNER, forwarded.get().path("operator_id").asLong());
        assertEquals(AGENT, forwarded.get().path("agent_id").asText());
        assertEquals("affect_system", forwarded.get().path("request").path("action").asText());
        assertEquals("status", forwarded.get().path("request").path("operation").path("action").asText());
    }

    @Test void onlyServerSecretCanProveTheCurrentAgentOwner() throws Exception {
        var proof = Map.of("operator_id", OWNER, "agent_id", AGENT);
        String secret = params.getValue(Constant.SERVER_SECRET, true);
        JsonNode result = post("/config/mia-management/verify", proof, secret);
        assertEquals(0, result.path("code").asInt());
        assertEquals("user:" + OWNER + ":agent:" + AGENT, result.path("data").path("owner").asText());
        assertNotEquals(0, post("/config/mia-management/verify", Map.of("operator_id", OTHER, "agent_id", AGENT), secret).path("code").asInt());
        assertNotEquals(0, post("/config/mia-management/verify", proof, token(OWNER)).path("code").asInt());
        db.update("UPDATE sys_user SET status=0 WHERE id=?", OWNER);
        assertNotEquals(0, post("/config/mia-management/verify", proof, secret).path("code").asInt());
    }

    @Test void ownerCanResetOnlyYoumiEmotionWithoutEditingRole() throws Exception {
        JsonNode result = post("/agent/" + AGENT + "/mia-settings", Map.of("action", "reset_mia_emotion"), token(OWNER));
        assertEquals(0, result.path("code").asInt());
        assertEquals("reset_mia_emotion", forwarded.get().path("request").path("action").asText());
        assertEquals("", db.queryForObject("SELECT system_prompt FROM ai_agent WHERE id=?", String.class, AGENT));
        assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", Map.of("action", "reset_mia_emotion"), token(OTHER)).path("code").asInt());
        assertEquals(1, calls.get());
    }

    @Test void memoryManagementUsesTheSameOwnerAuthorization() throws Exception {
        for (String action : new String[]{"memory_list", "memory_delete", "set_character"}) {
            var command = Map.of("action", action, "id", "synthetic-memory");
            assertEquals(0, post("/agent/" + AGENT + "/mia-settings", command, token(OWNER)).path("code").asInt());
            assertEquals(OWNER, forwarded.get().path("operator_id").asLong());
            assertEquals(action, forwarded.get().path("request").path("action").asText());
            assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", command, token(OTHER)).path("code").asInt());
            assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", command, null).path("code").asInt());
        }
        assertEquals(3, calls.get());
    }

    @Test void invalidOrDestructiveActionsAreNotProxied() throws Exception {
        for (var command : new Map[]{Map.of(), Map.of("action", "clear"), Map.of("action", "replace_role")}) {
            assertNotEquals(0, post("/agent/" + AGENT + "/mia-settings", command, token(OWNER)).path("code").asInt());
        }
        assertEquals(0, calls.get());
    }
}
