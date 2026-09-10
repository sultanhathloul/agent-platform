package sa.ncai.agent.gateway.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import sa.ncai.agent.gateway.GatewayTestSupport;

/** CORS preflight and request-id handling. */
class HttpBehaviourTest extends GatewayTestSupport {

    private static final String WEB_ORIGIN = "http://localhost:3000";

    @Test
    void preflightFromWebOriginIsAllowed() throws Exception {
        mvc.perform(options("/v1/chat/turns")
                        .header(HttpHeaders.ORIGIN, WEB_ORIGIN)
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "POST")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_HEADERS, "authorization,content-type"))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ACCESS_CONTROL_ALLOW_ORIGIN, WEB_ORIGIN));
    }

    @Test
    void preflightFromOtherOriginIsRejected() throws Exception {
        mvc.perform(options("/v1/chat/turns")
                        .header(HttpHeaders.ORIGIN, "http://evil.example")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "POST"))
                .andExpect(status().isForbidden());
    }

    @Test
    void requestIdHeaderIsExposedToTheBrowser() throws Exception {
        mvc.perform(get("/v1/me").header(HttpHeaders.ORIGIN, WEB_ORIGIN)
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ACCESS_CONTROL_EXPOSE_HEADERS,
                        org.hamcrest.Matchers.containsString(RequestIdFilter.HEADER)));
    }

    @Test
    void incomingUuidRequestIdIsKept() throws Exception {
        String id = "7d0c8a57-2c1e-4c4f-9a37-0e2e8f6a1b2c";
        mvc.perform(get("/v1/me").header(RequestIdFilter.HEADER, id)
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(header().string(RequestIdFilter.HEADER, id))
                .andExpect(jsonPath("$.request_id").value(id));
    }

    @Test
    void nonUuidRequestIdIsReplaced() throws Exception {
        mvc.perform(get("/v1/me").header(RequestIdFilter.HEADER, "abc\r\ninjected")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(header().string(RequestIdFilter.HEADER,
                        org.hamcrest.Matchers.matchesPattern("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")));
    }
}
