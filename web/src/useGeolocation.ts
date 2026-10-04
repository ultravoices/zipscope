import { useEffect, useRef, useState } from "react";

export type GeoStatus = "idle" | "requesting" | "granted" | "denied";

/**
 * One-shot browser geolocation. Calls onFix exactly once on success.
 * Respects reduced motion / unsupported browsers by failing to "denied".
 */
export function useGeolocation(onFix: (lat: number, lon: number) => void): GeoStatus {
  const [status, setStatus] = useState<GeoStatus>("idle");
  const cb = useRef(onFix);
  cb.current = onFix;

  useEffect(() => {
    if (!("geolocation" in navigator)) {
      setStatus("denied");
      return;
    }
    setStatus("requesting");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setStatus("granted");
        cb.current(pos.coords.latitude, pos.coords.longitude);
      },
      () => setStatus("denied"),
      { timeout: 10_000 }
    );
  }, []);

  return status;
}
