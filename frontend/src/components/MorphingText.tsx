import { useEffect, useState } from "react";

type Props = {
  words: string[];
  className?: string;
  interval?: number;
};

/** Cycles through `words`, morphing each one out then the next one in,
 * character by character, with a blinking gradient cursor. */
export function MorphingText({ words, className, interval = 3000 }: Props) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [displayText, setDisplayText] = useState(words[0] ?? "");

  const currentWord = words[currentIndex];
  const nextWord = words[(currentIndex + 1) % words.length];

  useEffect(() => {
    const morphDuration = 800;
    const steps = 20;
    let step = 0;

    const morph = setInterval(() => {
      step++;
      const progress = step / steps;
      if (progress < 0.5) {
        const count = Math.floor(currentWord.length * (1 - progress * 2));
        setDisplayText(currentWord.slice(0, count));
      } else {
        const count = Math.floor(nextWord.length * ((progress - 0.5) * 2));
        setDisplayText(nextWord.slice(0, count));
      }
      if (step >= steps) {
        clearInterval(morph);
        setDisplayText(nextWord);
      }
    }, morphDuration / steps);

    const wordTimeout = setTimeout(
      () => setCurrentIndex((i) => (i + 1) % words.length),
      interval,
    );

    return () => {
      clearInterval(morph);
      clearTimeout(wordTimeout);
    };
  }, [currentIndex, currentWord, nextWord, interval, words.length]);

  return (
    <span className={`morph ${className ?? ""}`}>
      <span className="morph__text">{displayText}</span>
      <span className="morph__cursor" aria-hidden="true" />
    </span>
  );
}
