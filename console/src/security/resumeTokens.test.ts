import { describe, expect, it } from "vitest";

import { ResumeTokenVault } from "./resumeTokens";

describe("ResumeTokenVault", () => {
  it("keeps tokens only for the current in-memory session and supports explicit disposal", () => {
    const vault = new ResumeTokenVault();
    vault.store("hld_1", "secret");

    expect(vault.get("hld_1")).toBe("secret");
    vault.delete("hld_1");
    expect(vault.get("hld_1")).toBeUndefined();

    vault.store("hld_2", "other");
    vault.clear();
    expect(vault.get("hld_2")).toBeUndefined();
  });
});
