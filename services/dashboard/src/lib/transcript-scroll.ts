type ScrollBehaviorOption = "auto" | "smooth";

interface CenteredScrollTopInput {
  scrollTop: number;
  scrollHeight: number;
  containerTop: number;
  containerHeight: number;
  itemTop: number;
  itemHeight: number;
}

export function calculateCenteredScrollTop({
  scrollTop,
  scrollHeight,
  containerTop,
  containerHeight,
  itemTop,
  itemHeight,
}: CenteredScrollTopInput): number {
  const itemOffsetWithinContainer = itemTop - containerTop;
  const centeredOffset = (containerHeight - itemHeight) / 2;
  const requestedScrollTop = scrollTop + itemOffsetWithinContainer - centeredOffset;
  const maxScrollTop = Math.max(0, scrollHeight - containerHeight);

  return Math.min(maxScrollTop, Math.max(0, requestedScrollTop));
}

/**
 * Scroll a transcript item inside its own scroll container without moving
 * outer ancestors such as the meeting page and its recording seek bar.
 */
export function scrollTranscriptItemToCenter(
  container: HTMLElement,
  item: HTMLElement,
  behavior: ScrollBehaviorOption = "smooth"
): void {
  const containerRect = container.getBoundingClientRect();
  const itemRect = item.getBoundingClientRect();

  container.scrollTo({
    top: calculateCenteredScrollTop({
      scrollTop: container.scrollTop,
      scrollHeight: container.scrollHeight,
      containerTop: containerRect.top,
      containerHeight: container.clientHeight,
      itemTop: itemRect.top,
      itemHeight: itemRect.height,
    }),
    behavior,
  });
}
