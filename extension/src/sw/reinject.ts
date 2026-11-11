// When the extension is installed/updated/re-enabled, Chrome kills the old content scripts silently and does
// NOT inject new ones into already-open tabs. A tab that was mid-dub would stay ducked. Inject the current
// content script into open chophim watch tabs: on attach it restores the volume the old instance stashed on
// the <video> element (data-voxsync-user-vol) and resumes dubbing.
export async function reinjectOpenTabs(): Promise<void> {
  const tabs = await chrome.tabs.query({ url: "https://chophim.lol/xem/*" });
  await Promise.all(tabs.map((t) => t.id === undefined ? undefined :
    chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["content.js"] }).catch(() => undefined)));
}
