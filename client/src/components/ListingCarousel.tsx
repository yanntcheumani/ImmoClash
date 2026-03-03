import { useEffect, useMemo, useState } from "react";
import { assetUrl } from "../lib/runtime";

interface Props {
  imageUrls: string[];
  title: string;
}

export function ListingCarousel({ imageUrls, title }: Props) {
  const [index, setIndex] = useState(0);
  const resolvedUrls = useMemo(() => imageUrls.map((url) => assetUrl(url)), [imageUrls]);

  useEffect(() => {
    setIndex(0);
  }, [resolvedUrls]);

  const total = resolvedUrls.length;
  const current = useMemo(() => resolvedUrls[index] ?? "", [resolvedUrls, index]);

  if (!total) {
    return <div className="media-empty">Aucune image disponible</div>;
  }

  const goPrev = () => setIndex((prev) => (prev - 1 + total) % total);
  const goNext = () => setIndex((prev) => (prev + 1) % total);

  return (
    <div className="carousel">
      <img src={current} alt={title} className="carousel-image" />
      {total > 1 && (
        <div className="carousel-controls">
          <button type="button" onClick={goPrev}>
            ←
          </button>
          <span>
            {index + 1}/{total}
          </span>
          <button type="button" onClick={goNext}>
            →
          </button>
        </div>
      )}
    </div>
  );
}
