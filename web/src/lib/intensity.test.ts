import { describe, expect, it } from "vitest";
import { intensityLine, intensityMeaning, intensityName, intensityTone } from "./intensity";

describe("intensity labels", () => {
  it("reads like the brief once calibrated", () => {
    const i = { level: 4, numeral: "IV", label: "Light", score: 13, percentile: 75, calibrated: true };
    expect(intensityLine(i)).toBe("IV, light: busier than 75% of hours in the past year");
    expect(intensityName(i)).toBe("IV, light");
  });

  it("does not present a provisional scale as a measurement", () => {
    const i = { level: 7, numeral: "VII", label: "Very strong", score: 31, percentile: 99, calibrated: false };
    expect(intensityMeaning(i)).toBe("Score 31 on a provisional scale, not yet ranked against past hours.");
    expect(intensityMeaning(i)).not.toContain("99");
  });

  it("handles the bottom of the scale, which has no percentile", () => {
    const i = { level: 1, numeral: "I", label: "Not felt", score: 0, percentile: null, calibrated: true };
    expect(intensityMeaning(i)).toBe("Below every threshold on the scale.");
  });

  it("maps levels onto the Mercalli color ramp", () => {
    expect(intensityTone(1)).toBe("operational");
    expect(intensityTone(3)).toBe("maintenance");
    expect(intensityTone(5)).toBe("degraded");
    expect(intensityTone(7)).toBe("partial");
    expect(intensityTone(9)).toBe("major");
    expect(intensityTone(10)).toBe("critical");
  });
});
