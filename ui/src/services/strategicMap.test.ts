import { afterEach, describe, expect, it, vi } from "vitest";
import { updateStrategicTeam } from "./strategicMap";

afterEach(() => vi.unstubAllGlobals());
describe("structured maintenance errors", () => {
  it("shows the server's permission or identity explanation instead of object Object", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: {
      code: "IDENTITY_UNAVAILABLE", message: "访客身份服务暂时不可用，已有记录仍保留",
    } }), { status: 503 })));
    await expect(updateStrategicTeam("t", { attention: "未标记", contact: "未接触" }))
      .rejects.toThrow("访客身份服务暂时不可用，已有记录仍保留");
  });
});
