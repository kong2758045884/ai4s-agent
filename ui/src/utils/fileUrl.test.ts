import { afterEach, describe, expect, it, vi } from "vitest";

import { normalizeFileUrlForBrowser, normalizeToolBaseUrlForBrowser } from "./fileUrl";

describe("normalizeFileUrlForBrowser", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("uses the same-origin proxy for a configured preview API without rewriting unrelated files", () => {
    vi.stubEnv("DEV", true);
    vi.stubGlobal("window", { location: { host: "127.0.0.1:3003", hostname: "127.0.0.1", protocol: "http:" } });
    expect(normalizeToolBaseUrlForBrowser("http://127.0.0.1:1604")).toBe("http://127.0.0.1:3003/tool");
    expect(normalizeFileUrlForBrowser("http://127.0.0.1:9000/report.html")).toBe("http://127.0.0.1:9000/report.html");
  });

  it("should rewrite loopback tool url to current origin tool path", () => {
    vi.stubGlobal("window", {
      location: {
        host: "localhost:3000",
        hostname: "localhost",
        protocol: "http:",
      },
    });

    expect(
      normalizeFileUrlForBrowser("http://127.0.0.1:1601/v1/file_tool/preview/req/demo.html")
    ).toBe("http://localhost:3000/tool/v1/file_tool/preview/req/demo.html");
  });
});
