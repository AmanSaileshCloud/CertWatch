import { useEffect, useRef, useState } from "react";

/* Workmates palette */
const INDIGO = "#5B43E8";
const NAVY = "#221B4D";
const AMBER = "#F9B21E";
const GOLD = "#FFC64D";
const PUPIL = "#221B4D";

type EyeProps = {
  size?: number;
  pupilSize?: number;
  maxDistance?: number;
  eyeColor?: string;
  pupilColor?: string;
  isBlinking?: boolean;
  forceLookX?: number;
  forceLookY?: number;
};

function useMouse() {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  useEffect(() => {
    const h = (e: MouseEvent) => setPos({ x: e.clientX, y: e.clientY });
    window.addEventListener("mousemove", h);
    return () => window.removeEventListener("mousemove", h);
  }, []);
  return pos;
}

function offsetTowards(
  ref: React.RefObject<HTMLDivElement | null>,
  mouse: { x: number; y: number },
  maxDistance: number,
  forceX?: number,
  forceY?: number,
) {
  if (forceX !== undefined && forceY !== undefined) return { x: forceX, y: forceY };
  if (!ref.current) return { x: 0, y: 0 };
  const r = ref.current.getBoundingClientRect();
  const dx = mouse.x - (r.left + r.width / 2);
  const dy = mouse.y - (r.top + r.height / 2);
  const dist = Math.min(Math.hypot(dx, dy), maxDistance);
  const a = Math.atan2(dy, dx);
  return { x: Math.cos(a) * dist, y: Math.sin(a) * dist };
}

function Pupil({ size = 12, maxDistance = 5, pupilColor = PUPIL, forceLookX, forceLookY }: EyeProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mouse = useMouse();
  const p = offsetTowards(ref, mouse, maxDistance, forceLookX, forceLookY);
  return (
    <div
      ref={ref}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        backgroundColor: pupilColor,
        transform: `translate(${p.x}px, ${p.y}px)`,
        transition: "transform 0.1s ease-out",
      }}
    />
  );
}

function EyeBall({
  size = 18,
  pupilSize = 7,
  maxDistance = 5,
  eyeColor = "white",
  pupilColor = PUPIL,
  isBlinking = false,
  forceLookX,
  forceLookY,
}: EyeProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mouse = useMouse();
  const p = offsetTowards(ref, mouse, maxDistance, forceLookX, forceLookY);
  return (
    <div
      ref={ref}
      style={{
        width: size,
        height: isBlinking ? 2 : size,
        backgroundColor: eyeColor,
        borderRadius: "50%",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "all 0.15s",
      }}
    >
      {!isBlinking && (
        <div
          style={{
            width: pupilSize,
            height: pupilSize,
            borderRadius: "50%",
            backgroundColor: pupilColor,
            transform: `translate(${p.x}px, ${p.y}px)`,
            transition: "transform 0.1s ease-out",
          }}
        />
      )}
    </div>
  );
}

type Props = {
  isTyping: boolean;
  passwordVisible: boolean;
  passwordLength: number;
};

export function LoginCharacters({ isTyping, passwordVisible, passwordLength }: Props) {
  const mouse = useMouse();
  const [purpleBlink, setPurpleBlink] = useState(false);
  const [blackBlink, setBlackBlink] = useState(false);
  const [lookAtEachOther, setLookAtEachOther] = useState(false);
  const [peeking, setPeeking] = useState(false);

  const indigoRef = useRef<HTMLDivElement>(null);
  const navyRef = useRef<HTMLDivElement>(null);
  const amberRef = useRef<HTMLDivElement>(null);
  const goldRef = useRef<HTMLDivElement>(null);

  const peeking_revealing = passwordLength > 0 && passwordVisible;
  const hiding = isTyping || (passwordLength > 0 && !passwordVisible);

  // random blinks
  useEffect(() => makeBlinker(setPurpleBlink), []);
  useEffect(() => makeBlinker(setBlackBlink), []);

  // glance at each other while typing
  useEffect(() => {
    if (!isTyping) return setLookAtEachOther(false);
    setLookAtEachOther(true);
    const t = setTimeout(() => setLookAtEachOther(false), 800);
    return () => clearTimeout(t);
  }, [isTyping]);

  // indigo peeks when password is revealed
  useEffect(() => {
    if (!peeking_revealing) return setPeeking(false);
    const t = setTimeout(() => {
      setPeeking(true);
      setTimeout(() => setPeeking(false), 800);
    }, Math.random() * 3000 + 2000);
    return () => clearTimeout(t);
  }, [peeking_revealing, peeking]);

  const lean = (ref: React.RefObject<HTMLDivElement | null>) => {
    if (!ref.current) return { faceX: 0, faceY: 0, skew: 0 };
    const r = ref.current.getBoundingClientRect();
    const dx = mouse.x - (r.left + r.width / 2);
    const dy = mouse.y - (r.top + r.height / 3);
    return {
      faceX: Math.max(-15, Math.min(15, dx / 20)),
      faceY: Math.max(-10, Math.min(10, dy / 30)),
      skew: Math.max(-6, Math.min(6, -dx / 120)),
    };
  };

  const ip = lean(indigoRef);
  const np = lean(navyRef);
  const ap = lean(amberRef);
  const gp = lean(goldRef);

  const indigoEye = {
    forceLookX: peeking_revealing ? (peeking ? 4 : -4) : lookAtEachOther ? 3 : undefined,
    forceLookY: peeking_revealing ? (peeking ? 5 : -4) : lookAtEachOther ? 4 : undefined,
  };
  const navyEye = {
    forceLookX: peeking_revealing ? -4 : lookAtEachOther ? 0 : undefined,
    forceLookY: peeking_revealing ? -4 : lookAtEachOther ? -4 : undefined,
  };

  return (
    <div style={{ position: "relative", width: 550, height: 400 }}>
      {/* Indigo tall — back */}
      <div
        ref={indigoRef}
        style={{
          position: "absolute",
          bottom: 0,
          left: 70,
          width: 180,
          height: hiding ? 440 : 400,
          backgroundColor: INDIGO,
          borderRadius: "10px 10px 0 0",
          zIndex: 1,
          transformOrigin: "bottom center",
          transition: "all 0.7s ease-in-out",
          transform: peeking_revealing
            ? "skewX(0deg)"
            : hiding
              ? `skewX(${ip.skew - 12}deg) translateX(40px)`
              : `skewX(${ip.skew}deg)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            display: "flex",
            gap: 32,
            transition: "all 0.7s ease-in-out",
            left: peeking_revealing ? 20 : lookAtEachOther ? 55 : 45 + ip.faceX,
            top: peeking_revealing ? 35 : lookAtEachOther ? 65 : 40 + ip.faceY,
          }}
        >
          <EyeBall isBlinking={purpleBlink} {...indigoEye} />
          <EyeBall isBlinking={purpleBlink} {...indigoEye} />
        </div>
      </div>

      {/* Navy tall — middle */}
      <div
        ref={navyRef}
        style={{
          position: "absolute",
          bottom: 0,
          left: 240,
          width: 120,
          height: 310,
          backgroundColor: NAVY,
          borderRadius: "8px 8px 0 0",
          zIndex: 2,
          transformOrigin: "bottom center",
          transition: "all 0.7s ease-in-out",
          transform: peeking_revealing
            ? "skewX(0deg)"
            : lookAtEachOther
              ? `skewX(${np.skew * 1.5 + 10}deg) translateX(20px)`
              : `skewX(${np.skew * 1.5}deg)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            display: "flex",
            gap: 24,
            transition: "all 0.7s ease-in-out",
            left: peeking_revealing ? 10 : lookAtEachOther ? 32 : 26 + np.faceX,
            top: peeking_revealing ? 28 : lookAtEachOther ? 12 : 32 + np.faceY,
          }}
        >
          <EyeBall size={16} pupilSize={6} maxDistance={4} isBlinking={blackBlink} {...navyEye} />
          <EyeBall size={16} pupilSize={6} maxDistance={4} isBlinking={blackBlink} {...navyEye} />
        </div>
      </div>

      {/* Amber semicircle — front left */}
      <div
        ref={amberRef}
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          width: 240,
          height: 200,
          backgroundColor: AMBER,
          borderRadius: "120px 120px 0 0",
          zIndex: 3,
          transformOrigin: "bottom center",
          transition: "all 0.7s ease-in-out",
          transform: peeking_revealing ? "skewX(0deg)" : `skewX(${ap.skew}deg)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            display: "flex",
            gap: 32,
            transition: "all 0.2s ease-out",
            left: peeking_revealing ? 50 : 82 + ap.faceX,
            top: peeking_revealing ? 85 : 90 + ap.faceY,
          }}
        >
          <Pupil forceLookX={peeking_revealing ? -5 : undefined} forceLookY={peeking_revealing ? -4 : undefined} />
          <Pupil forceLookX={peeking_revealing ? -5 : undefined} forceLookY={peeking_revealing ? -4 : undefined} />
        </div>
      </div>

      {/* Gold tall — front right */}
      <div
        ref={goldRef}
        style={{
          position: "absolute",
          bottom: 0,
          left: 310,
          width: 140,
          height: 230,
          backgroundColor: GOLD,
          borderRadius: "70px 70px 0 0",
          zIndex: 4,
          transformOrigin: "bottom center",
          transition: "all 0.7s ease-in-out",
          transform: peeking_revealing ? "skewX(0deg)" : `skewX(${gp.skew}deg)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            display: "flex",
            gap: 24,
            transition: "all 0.2s ease-out",
            left: peeking_revealing ? 20 : 52 + gp.faceX,
            top: peeking_revealing ? 35 : 40 + gp.faceY,
          }}
        >
          <Pupil forceLookX={peeking_revealing ? -5 : undefined} forceLookY={peeking_revealing ? -4 : undefined} />
          <Pupil forceLookX={peeking_revealing ? -5 : undefined} forceLookY={peeking_revealing ? -4 : undefined} />
        </div>
        <div
          style={{
            position: "absolute",
            width: 80,
            height: 4,
            backgroundColor: PUPIL,
            borderRadius: 999,
            transition: "all 0.2s ease-out",
            left: peeking_revealing ? 10 : 40 + gp.faceX,
            top: peeking_revealing ? 88 : 88 + gp.faceY,
          }}
        />
      </div>
    </div>
  );
}

function makeBlinker(setter: (v: boolean) => void): () => void {
  let inner: ReturnType<typeof setTimeout>;
  const schedule = () => {
    const t = setTimeout(() => {
      setter(true);
      inner = setTimeout(() => {
        setter(false);
        schedule();
      }, 150);
    }, Math.random() * 4000 + 3000);
    inner = t;
    return t;
  };
  const first = schedule();
  return () => {
    clearTimeout(first);
    clearTimeout(inner);
  };
}
