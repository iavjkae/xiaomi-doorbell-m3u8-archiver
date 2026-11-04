const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class Element extends EventTarget {
  constructor() {
    super();
    this.children = [];
    this.clientWidth = 600;
  }
  attachShadow() { this.shadowRoot = new Element(); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  querySelector() { return null; }
}

function loadCard() {
  const registry = new Map();
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    "../../custom_components/xiaomi_doorbell_m3u8_archiver/frontend/archive-card.js"), "utf8"), {
    HTMLElement: Element,
    CustomEvent,
    document: { createElement: () => new Element() },
    customElements: { get: (tag) => registry.get(tag), define: (tag, type) => registry.set(tag, type) },
    window: {},
  });
  return {
    Card: registry.get("xiaomi-doorbell-archive-card"),
    Editor: registry.get("xiaomi-doorbell-archive-card-editor"),
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function playbackCard() {
  const { Card } = loadCard();
  const card = new Card();
  card._render = () => {};
  return card;
}

test("only the latest playback request may select a video", async () => {
  for (const staleFails of [false, true]) {
    const card = playbackCard();
    const first = deferred();
    const second = deferred();
    card._hass = { callWS: ({ media_content_id }) => media_content_id === "A" ? first.promise : second.promise };
    const playA = card._play({ media_content_id: "A" });
    const playB = card._play({ media_content_id: "B" });
    if (staleFails) first.reject(new Error("stale failure"));
    else first.resolve({ url: "A.mp4" });
    await playA;
    assert.equal(card._loadingVideo, "B");
    assert.equal(card._error, null);
    second.resolve({ url: "B.mp4" });
    await playB;
    assert.equal(card._activeVideo, "B");
    assert.equal(card._activeVideoUrl, "B.mp4");
  }
  const card = playbackCard();
  const first = deferred();
  card._hass = { callWS: ({ media_content_id }) => media_content_id === "A" ? first.promise : Promise.resolve({ url: "B.mp4" }) };
  const playA = card._play({ media_content_id: "A" });
  await card._play({ media_content_id: "B" });
  first.resolve({ url: "A.mp4" });
  await playA;
  assert.equal(card._activeVideo, "B");
  assert.equal(card._activeVideoUrl, "B.mp4");
});

test("resolved videos start playback and current resolve failures are shown", async () => {
  const card = playbackCard();
  let plays = 0;
  card.shadowRoot.querySelector = () => ({ play: async () => { plays++; } });
  card._hass = { callWS: async () => ({ url: "A.mp4" }) };
  await card._play({ media_content_id: "A" });
  assert.equal(plays, 1);
  assert.equal(card._activeVideo, "A");
  assert.equal(card._loadingVideo, null);
  card._hass.callWS = async () => { throw new Error("resolve failed"); };
  await card._play({ media_content_id: "B" });
  assert.equal(plays, 1);
  assert.match(card._error, /resolve failed$/);
  assert.equal(card._loadingVideo, null);
});

test("refresh, archive changes and disconnect invalidate pending playback", async () => {
  for (const action of ["refresh", "archive", "disconnect"]) {
    const card = playbackCard();
    const pending = deferred();
    card._loaded = true;
    card._hass = { callWS: ({ type }) => type === "media_source/resolve_media"
      ? pending.promise : Promise.resolve({ children: [] }) };
    const play = card._play({ media_content_id: "A" });
    if (action === "refresh") await card._load();
    else if (action === "archive") card.setConfig({ entry_id: "other" });
    else card.disconnectedCallback();
    pending.resolve({ url: "A.mp4" });
    await play;
    assert.equal(card._activeVideo, null);
    assert.equal(card._activeVideoUrl, null);
    assert.equal(card._loadingVideo, null);
  }
});

test("reconnecting browses again while hass updates do not poll", async () => {
  const card = playbackCard();
  let calls = 0;
  const hass = { callWS: async () => ({ children: [{ can_play: true, title: `visit-${++calls}` }] }) };
  card.hass = hass;
  card.connectedCallback();
  await new Promise(setImmediate);
  assert.equal(card._items[0].title, "visit-1");
  card.hass = hass;
  card.hass = hass;
  assert.equal(calls, 1);
  card.disconnectedCallback();
  card.connectedCallback();
  await new Promise(setImmediate);
  assert.equal(calls, 2);
  assert.equal(card._items[0].title, "visit-2");
});

test("a browse response from a previous visit cannot overwrite the new visit", async () => {
  for (const staleFails of [false, true]) {
    const card = playbackCard();
    const oldVisit = deferred();
    const newVisit = deferred();
    let calls = 0;
    card._hass = { callWS: () => ++calls === 1 ? oldVisit.promise : newVisit.promise };
    card.connectedCallback();
    card.disconnectedCallback();
    card.connectedCallback();
    if (staleFails) oldVisit.reject(new Error("old visit failed"));
    else oldVisit.resolve({ children: [{ can_play: true, title: "old" }] });
    await new Promise(setImmediate);
    assert.equal(card._loading, true);
    assert.equal(card._error, null);
    newVisit.resolve({ children: [{ can_play: true, title: "new" }] });
    await new Promise(setImmediate);
    assert.equal(card._items[0].title, "new");
    assert.equal(card._loading, false);
  }
});

test("defaults retain automatic layout and widescreen previews", () => {
  const { Card } = loadCard();
  const card = new Card();
  card.setConfig({ type: "custom:xiaomi-doorbell-archive-card" });
  assert.equal(card._config.columns, 0);
  assert.equal(card._config.min_video_width, 260);
  assert.equal(card._config.aspect_ratio, "16:9");
  assert.match(card.shadowRoot.children[0].textContent, /repeat\(auto-fill/);
});

test("configured layout is applied without fetching media", () => {
  const { Card } = loadCard();
  const card = new Card();
  card._loaded = true;
  card._load = () => assert.fail("Layout changes must not rescan media");
  card.setConfig({ columns: 3, aspect_ratio: "1:1", min_video_width: 180 });
  const style = card.shadowRoot.children[0].textContent;
  assert.match(style, /repeat\(3, minmax\(0, 1fr\)\)/);
  assert.equal((style.match(/aspect-ratio: 1 \/ 1/g) || []).length, 2);
  assert.ok(Number.isFinite(card.getCardSize()));
  card.setConfig({ columns: 0, min_video_width: 180 });
  assert.match(card.shadowRoot.children[0].textContent, /min\(100%, 180px\)/);
});

test("invalid layout values cannot enter CSS", () => {
  const { Card } = loadCard();
  for (const config of [
    { columns: -1 }, { columns: 7 }, { columns: 1.5 }, { columns: "2" },
    { min_video_width: 119 }, { min_video_width: 641 }, { min_video_width: "260px" },
    { aspect_ratio: "auto; color: red" },
  ]) {
    assert.throws(() => new Card().setConfig(config));
  }
});

test("visual editor emits HA config changes and preserves other options", () => {
  const { Editor } = loadCard();
  const editor = new Editor();
  editor.hass = { locale: { language: "zh-Hans" } };
  editor.setConfig({ type: "custom:xiaomi-doorbell-archive-card", title: "Door", entry_id: "entry-1" });
  assert.ok(editor._form.schema.some((field) => field.name === "min_video_width"));
  let changed;
  editor.addEventListener("config-changed", (event) => { changed = event; });
  editor._form.dispatchEvent(new CustomEvent("value-changed", { detail: { value: { columns: 2 } } }));
  assert.equal(changed.detail.config.columns, 2);
  assert.equal(changed.detail.config.entry_id, "entry-1");
  assert.equal(changed.detail.config.title, "Door");
  assert.equal(changed.bubbles, true);
  assert.equal(changed.composed, true);
  assert.ok(!editor._form.schema.some((field) => field.name === "min_video_width"));
  assert.equal(editor._form.computeLabel({ name: "aspect_ratio" }), "画面比例");
});

test("archive picker uses HA's native selector filtered to this integration", () => {
  const { Editor } = loadCard();
  const editor = new Editor();
  editor.hass = { locale: { language: "zh-Hans" } };
  editor.setConfig({});
  const field = editor._form.schema.find((item) => item.name === "entry_id");
  assert.equal(field.selector.config_entry.integration, "xiaomi_doorbell_m3u8_archiver");
  assert.ok(!field.required);
  assert.equal(editor._form.computeLabel(field), "归档实例（留空显示全部）");
});

test("archive selection can be changed and cleared without resetting layout", () => {
  const { Card, Editor } = loadCard();
  for (const empty of [undefined, null, ""]) {
    const editor = new Editor();
    editor.setConfig({ entry_id: "old-entry", columns: 3 });
    let config;
    editor.addEventListener("config-changed", (event) => { config = event.detail.config; });
    editor._form.dispatchEvent(new CustomEvent("value-changed", {
      detail: { value: { entry_id: "new-entry" } },
    }));
    assert.equal(config.entry_id, "new-entry");
    editor._form.dispatchEvent(new CustomEvent("value-changed", {
      detail: { value: { entry_id: empty } },
    }));
    assert.ok(!Object.hasOwn(config, "entry_id"));
    assert.equal(config.columns, 3);
    const card = new Card();
    card.setConfig(config);
    assert.ok(card._matchesConfiguredEntry({ media_content_id: "any-entry" }));
  }
});
