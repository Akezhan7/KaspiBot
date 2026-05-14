/**
 * Pagination — компактная пагинация с номерами страниц и эллипсисом.
 *
 * Особенности:
 *   - Кнопки «« (первая) / »» (последняя) для прыжка в края.
 *   - До 5 номеров страниц вокруг текущей с эллипсисом.
 *   - Inline-инпут «перейти к странице N» — для длинных списков.
 *
 * Все номера страниц 1-based для UX; offset считает родитель.
 */
import { useState, useCallback, useEffect, useMemo } from "react";

interface Props {
  currentPage: number;       // 1-based
  totalPages: number;
  onPageChange: (page: number) => void;
}

/** Сформировать массив номеров страниц с эллипсисами. */
function buildPageRange(current: number, total: number): (number | "…")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  const range: (number | "…")[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);

  if (left > 2) range.push("…");
  for (let p = left; p <= right; p++) range.push(p);
  if (right < total - 1) range.push("…");

  range.push(total);
  return range;
}

export default function Pagination({ currentPage, totalPages, onPageChange }: Props) {
  const [jumpValue, setJumpValue] = useState(String(currentPage));

  useEffect(() => {
    setJumpValue(String(currentPage));
  }, [currentPage]);

  const pages = useMemo(() => buildPageRange(currentPage, totalPages), [currentPage, totalPages]);

  const jump = useCallback(() => {
    const n = Number(jumpValue);
    if (!Number.isFinite(n) || n < 1) {
      setJumpValue(String(currentPage));
      return;
    }
    const clamped = Math.max(1, Math.min(totalPages, Math.floor(n)));
    onPageChange(clamped);
  }, [jumpValue, totalPages, currentPage, onPageChange]);

  if (totalPages <= 1) return null;

  return (
    <div className="pagination">
      <button
        className="page-btn"
        disabled={currentPage === 1}
        onClick={() => onPageChange(1)}
        title="К первой странице"
      >
        «
      </button>
      <button
        className="page-btn"
        disabled={currentPage === 1}
        onClick={() => onPageChange(currentPage - 1)}
        title="Назад"
      >
        ‹
      </button>

      {pages.map((p, idx) =>
        p === "…" ? (
          <span key={`ell-${idx}`} className="page-ellipsis">…</span>
        ) : (
          <button
            key={p}
            className={`page-btn page-num${p === currentPage ? " active" : ""}`}
            onClick={() => onPageChange(p)}
          >
            {p}
          </button>
        ),
      )}

      <button
        className="page-btn"
        disabled={currentPage === totalPages}
        onClick={() => onPageChange(currentPage + 1)}
        title="Вперёд"
      >
        ›
      </button>
      <button
        className="page-btn"
        disabled={currentPage === totalPages}
        onClick={() => onPageChange(totalPages)}
        title="К последней странице"
      >
        »
      </button>

      {totalPages > 10 && (
        <form
          className="page-jump"
          onSubmit={(e) => {
            e.preventDefault();
            jump();
          }}
        >
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            className="page-jump-input"
            value={jumpValue}
            onChange={(e) => setJumpValue(e.target.value.replace(/[^0-9]/g, ""))}
            onBlur={jump}
            aria-label="Перейти к странице"
          />
          <span className="page-jump-total">/ {totalPages}</span>
        </form>
      )}
    </div>
  );
}
