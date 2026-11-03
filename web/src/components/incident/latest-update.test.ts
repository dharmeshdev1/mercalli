import { describe, expect, it } from "vitest";
import { cleanBody } from "./latest-update";

describe("cleanBody", () => {
  it("drops the HTML some vendors post and keeps the words", () => {
    expect(cleanBody("We are investigating a problem with our <strong>Switzerland</strong> region.<br>More soon &amp; thanks")).toBe(
      "We are investigating a problem with our Switzerland region.\nMore soon & thanks",
    );
  });

  it("removes markdown emphasis and extra blank lines", () => {
    expect(cleanBody("**Update**\n\n\n\nFixed")).toBe("Update\n\nFixed");
  });
});
