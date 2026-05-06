/**
 * useViewport — track viewport width with debounce + breakpoint flags.
 *
 * Breakpoints (mobile-first):
 *   < 600  → mobile  (phones)
 *   < 1024 → tablet  (iPad portrait)
 *   ≥ 1024 → desktop (laptop+)
 *
 * Use sparingly — for purely visual grid stacking, prefer the
 * pure-CSS `responsiveGridCols(minWidth)` helper which auto-stacks
 * via grid-template-columns: repeat(auto-fit, minmax(...)).
 *
 * This hook is for cases where layout *logic* must branch
 * (e.g., hide a button on mobile, swap nav for bottom-strip).
 */

import { useState, useEffect } from "react";

export const BP = {
  MOBILE: 600,
  TABLET: 1024,
};

export default function useViewport() {
  const [width, setWidth] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth : 1440
  );

  useEffect(() => {
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setWidth(window.innerWidth));
    };
    window.addEventListener("resize", onResize, { passive: true });
    onResize();
    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(raf);
    };
  }, []);

  return {
    width,
    isMobile: width < BP.MOBILE,
    isTablet: width >= BP.MOBILE && width < BP.TABLET,
    isDesktop: width >= BP.TABLET,
    isPhoneOrTablet: width < BP.TABLET,
  };
}

/**
 * Pure-CSS auto-stacking grid columns.
 * Usage: gridTemplateColumns: responsiveGridCols(280)
 *   → 2-up on desktop (>=580px), 1-up on mobile.
 *   → 3-up if you pass 200, etc.
 *
 * The min(...,100%) protects against horizontal overflow on tiny screens.
 */
export const responsiveGridCols = (minWidth = 280) =>
  `repeat(auto-fit, minmax(min(${minWidth}px, 100%), 1fr))`;
