// F3 cosmetic part: while ad blocking is on, inject the hide-banners stylesheet and click the player's own
// "Bỏ qua" (skip ad) button if a pre-roll still slips through. Network blocking is the DNR ruleset in the SW.

let link: HTMLLinkElement | null = null;
let observer: MutationObserver | null = null;

function clickSkip(): void {
  for (const b of document.querySelectorAll("button")) {
    if (/^\s*Bỏ qua/i.test(b.textContent ?? "") && (b as HTMLElement).offsetParent !== null) b.click();
  }
}

export function setAdBlocking(on: boolean): void {
  if (on && !link) {
    link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = chrome.runtime.getURL("chophim-cosmetic.css");
    (document.head ?? document.documentElement).append(link);
    observer = new MutationObserver(clickSkip);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    clickSkip();
  } else if (!on && link) {
    link.remove();
    link = null;
    observer?.disconnect();
    observer = null;
  }
}
