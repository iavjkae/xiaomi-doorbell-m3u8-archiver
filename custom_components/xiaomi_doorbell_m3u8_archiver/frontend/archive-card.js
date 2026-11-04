const CARD_TAG = "xiaomi-doorbell-archive-card";
const INTEGRATION_DOMAIN = "xiaomi_doorbell_m3u8_archiver";
const MEDIA_SOURCE_ID = `media-source://${INTEGRATION_DOMAIN}`;
const ASPECT_RATIOS = ["16:9", "4:3", "1:1"];

function layoutConfig(config) {
  const columns = config.columns ?? 0;
  const minWidth = config.min_video_width ?? 260;
  const aspectRatio = config.aspect_ratio ?? "16:9";
  if (!Number.isInteger(columns) || columns < 0 || columns > 6) {
    throw new Error("columns must be an integer between 0 and 6");
  }
  if (!Number.isInteger(minWidth) || minWidth < 120 || minWidth > 640) {
    throw new Error("min_video_width must be an integer between 120 and 640");
  }
  if (!ASPECT_RATIOS.includes(aspectRatio)) {
    throw new Error("aspect_ratio must be 16:9, 4:3 or 1:1");
  }
  return { ...config, columns, min_video_width: minWidth, aspect_ratio: aspectRatio };
}

class XiaomiDoorbellArchiveCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._form = document.createElement("ha-form");
    this._form.addEventListener("value-changed", (event) => {
      event.stopPropagation();
      const config = layoutConfig({ ...this._config, ...event.detail.value });
      if (!config.entry_id) delete config.entry_id;
      this._config = config;
      this._updateForm();
      this.dispatchEvent(new CustomEvent("config-changed", {
        detail: { config }, bubbles: true, composed: true,
      }));
    });
    this.shadowRoot.append(this._form);
  }

  setConfig(config) {
    this._config = layoutConfig(config);
    this._updateForm();
  }

  set hass(hass) {
    this._hass = hass;
    this._form.hass = hass;
    this._updateForm();
  }

  _updateForm() {
    if (!this._config) return;
    const zh = String(this._hass?.locale?.language || this._hass?.language || "en")
      .toLowerCase().startsWith("zh");
    const labels = zh ? {
      title: "标题", entry_id: "归档实例（留空显示全部）",
      columns: "每行视频数（0 为自动）", min_video_width: "视频最小宽度（像素）",
      aspect_ratio: "画面比例",
    } : {
      title: "Title", entry_id: "Archive instance (empty for all)",
      columns: "Videos per row (0 for automatic)", min_video_width: "Minimum video width (pixels)",
      aspect_ratio: "Aspect ratio",
    };
    this._form.computeLabel = (schema) => labels[schema.name] || schema.name;
    this._form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "entry_id", selector: { config_entry: { integration: INTEGRATION_DOMAIN } } },
      { name: "columns", required: true, selector: { number: { min: 0, max: 6, step: 1, mode: "box" } } },
      ...(this._config.columns === 0 ? [
        { name: "min_video_width", required: true, selector: { number: { min: 120, max: 640, step: 1, mode: "box", unit_of_measurement: "px" } } },
      ] : []),
      { name: "aspect_ratio", required: true, selector: { select: { options: ASPECT_RATIOS, mode: "dropdown" } } },
    ];
    this._form.data = this._config;
  }
}

class XiaomiDoorbellArchiveCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = layoutConfig({});
    this._items = [];
    this._activeVideo = null;
    this._activeVideoUrl = null;
    this._loaded = false;
    this._loading = false;
    this._loadingVideo = null;
    this._error = null;
    this._playRequest = 0;
    this._browseRequest = 0;
  }

  static getStubConfig() {
    return {};
  }

  static getConfigElement() {
    return document.createElement(`${CARD_TAG}-editor`);
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid card configuration");
    }

    const entryChanged = this._config.entry_id !== config.entry_id;
    this._config = layoutConfig(config);
    if (entryChanged) this._resetPlayback();
    if (entryChanged && this._loaded) {
      this._loaded = false;
      this._items = [];
      void this._load();
    } else {
      this._render();
    }
  }

  set hass(hass) {
    const firstUpdate = !this._hass;
    this._hass = hass;
    if (firstUpdate && this.isConnected) {
      void this._load();
    }
  }

  connectedCallback() {
    this._render();
    if (this._hass && !this._loading) {
      void this._load();
    }
  }

  disconnectedCallback() {
    this._browseRequest++;
    this._loading = false;
    this._loaded = false;
    this._resetPlayback();
  }

  _resetPlayback() {
    this._playRequest++;
    this._loadingVideo = null;
    this._activeVideo = null;
    this._activeVideoUrl = null;
  }

  getCardSize() {
    const width = Math.max(0, this.clientWidth - 32);
    const columns = this._config.columns || Math.max(1,
      Math.floor((width + 12) / (this._config.min_video_width + 12)));
    const [ratioWidth, ratioHeight] = this._config.aspect_ratio.split(":").map(Number);
    const tileWidth = width > 0
      ? Math.max(0, (width - (columns - 1) * 12) / columns)
      : this._config.min_video_width;
    const rows = Math.ceil(this._items.length / columns);
    const rowHeight = tileWidth * ratioHeight / ratioWidth + 68;
    return Math.max(2, Math.ceil((68 + rows * rowHeight) / 50));
  }

  async _load() {
    if (!this._hass || this._loading) {
      return;
    }

    const request = ++this._browseRequest;
    this._resetPlayback();
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const media = await this._hass.callWS({
        type: "media_source/browse_media",
        media_content_id: MEDIA_SOURCE_ID,
      });
      if (request !== this._browseRequest) return;
      const children = Array.isArray(media.children) ? media.children : [];
      this._items = children.filter(
        (item) => item.can_play && this._matchesConfiguredEntry(item),
      );
      this._loaded = true;
    } catch (error) {
      if (request !== this._browseRequest) return;
      this._error = error instanceof Error ? error.message : String(error);
    } finally {
      if (request === this._browseRequest) {
        this._loading = false;
        this._render();
      }
    }
  }

  _matchesConfiguredEntry(item) {
    if (!this._config.entry_id) {
      return true;
    }
    return item.media_content_id.includes(`/video/${this._config.entry_id}/`);
  }

  async _play(item) {
    if (this._loading) return;
    const request = ++this._playRequest;
    const id = item.media_content_id;
    this._loadingVideo = id;
    this._error = null;
    this._render();
    let resolved;
    try {
      resolved = await this._hass.callWS({
        type: "media_source/resolve_media",
        media_content_id: id,
      });
    } catch (error) {
      if (request !== this._playRequest) return;
      this._loadingVideo = null;
      this._error = error instanceof Error ? error.message : String(error);
      this._render();
      return;
    }

    if (request !== this._playRequest) return;
    this._loadingVideo = null;
    this._activeVideo = id;
    this._activeVideoUrl = resolved.url;
    this._render();
    const video = this.shadowRoot.querySelector("video");
    if (video) {
      void video.play().catch(() => undefined);
    }
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }

    const card = document.createElement("ha-card");
    const columns = this._config.columns;
    const gridColumns = columns
      ? `repeat(${columns}, minmax(0, 1fr))`
      : `repeat(auto-fill, minmax(min(100%, ${this._config.min_video_width}px), 1fr))`;
    const aspectRatio = this._config.aspect_ratio.replace(":", " / ");
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header {
        align-items: center;
        display: flex;
        gap: 12px;
        justify-content: space-between;
        min-height: 48px;
        padding: 12px 16px 8px;
      }
      .heading { min-width: 0; }
      .title {
        color: var(--primary-text-color);
        font-size: 16px;
        font-weight: 500;
        line-height: 24px;
      }
      .count {
        color: var(--secondary-text-color);
        font-size: 12px;
        line-height: 18px;
      }
      .icon-button {
        align-items: center;
        background: transparent;
        border: 0;
        border-radius: 50%;
        color: var(--primary-text-color);
        cursor: pointer;
        display: inline-flex;
        flex: 0 0 40px;
        height: 40px;
        justify-content: center;
        padding: 0;
        width: 40px;
      }
      .icon-button:hover { background: var(--secondary-background-color); }
      .icon-button:disabled { cursor: default; opacity: 0.45; }
      .content { padding: 8px 16px 16px; }
      .grid {
        display: grid;
        gap: 12px;
        grid-template-columns: ${gridColumns};
      }
      .video-item {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        min-width: 0;
        overflow: hidden;
      }
      .preview {
        align-items: center;
        aspect-ratio: ${aspectRatio};
        background: #17242d;
        border: 0;
        color: #fff;
        cursor: pointer;
        display: flex;
        justify-content: center;
        overflow: hidden;
        padding: 0;
        position: relative;
        width: 100%;
        box-sizing: border-box;
      }
      .thumbnail {
        height: 100%;
        object-fit: cover;
        width: 100%;
      }
      .play-icon {
        align-items: center;
        background: rgb(0 0 0 / 58%);
        border-radius: 50%;
        display: flex;
        aspect-ratio: 1;
        justify-content: center;
        position: absolute;
        width: min(56px, 45%);
      }
      .play-icon ha-icon { --mdc-icon-size: 100%; height: 70%; width: 70%; }
      .preview:hover .play-icon { background: rgb(0 0 0 / 72%); color: #30b8b2; }
      video {
        aspect-ratio: ${aspectRatio};
        height: auto;
        background: #000;
        display: block;
        object-fit: contain;
        width: 100%;
      }
      .video-title {
        color: var(--primary-text-color);
        font-size: 13px;
        line-height: 20px;
        overflow-wrap: anywhere;
        padding: 8px 10px;
      }
      .state {
        align-items: center;
        color: var(--secondary-text-color);
        display: flex;
        justify-content: center;
        min-height: 96px;
        padding: 16px;
        text-align: center;
      }
      .error { color: var(--error-color); }
      @media (max-width: 450px) {
        .header { padding-inline: 12px; }
        .content { padding: 8px 12px 12px; }
      }
    `;

    const header = document.createElement("div");
    header.className = "header";
    const heading = document.createElement("div");
    heading.className = "heading";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = this._config.title || this._text("title");
    const count = document.createElement("div");
    count.className = "count";
    count.textContent = this._text("count", this._items.length);
    heading.append(title, count);

    const refresh = document.createElement("button");
    refresh.className = "icon-button";
    refresh.type = "button";
    refresh.title = this._text("refresh");
    refresh.setAttribute("aria-label", this._text("refresh"));
    refresh.disabled = this._loading;
    const refreshIcon = document.createElement("ha-icon");
    refreshIcon.setAttribute("icon", "mdi:refresh");
    refresh.append(refreshIcon);
    refresh.addEventListener("click", () => void this._load());
    header.append(heading, refresh);

    const content = document.createElement("div");
    content.className = "content";
    if (this._loading && !this._loaded) {
      content.append(this._state(this._text("loading")));
    } else if (this._error) {
      content.append(this._state(this._text("error"), true));
    } else if (this._items.length === 0) {
      content.append(this._state(this._text("empty")));
    } else {
      const grid = document.createElement("div");
      grid.className = "grid";
      for (const item of this._items) {
        grid.append(this._videoItem(item));
      }
      content.append(grid);
    }

    card.append(header, content);
    this.shadowRoot.replaceChildren(style, card);
  }

  _videoItem(item) {
    const wrapper = document.createElement("article");
    wrapper.className = "video-item";
    const id = item.media_content_id;
    const resolvedUrl = this._activeVideo === id ? this._activeVideoUrl : null;

    if (this._activeVideo === id && resolvedUrl) {
      const video = document.createElement("video");
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      if (item.thumbnail) {
        video.poster = item.thumbnail;
      }
      video.src = resolvedUrl;
      wrapper.append(video);
    } else {
      const preview = document.createElement("button");
      preview.className = "preview";
      preview.type = "button";
      preview.title = this._text("play");
      preview.setAttribute("aria-label", `${this._text("play")}: ${item.title}`);
      preview.disabled = this._loadingVideo === id;
      if (item.thumbnail) {
        const thumbnail = document.createElement("img");
        thumbnail.className = "thumbnail";
        thumbnail.src = item.thumbnail;
        thumbnail.alt = "";
        thumbnail.loading = "lazy";
        preview.append(thumbnail);
      }
      const playIcon = document.createElement("span");
      playIcon.className = "play-icon";
      const icon = document.createElement("ha-icon");
      icon.setAttribute("icon", this._loadingVideo === id ? "mdi:loading" : "mdi:play-circle");
      playIcon.append(icon);
      preview.append(playIcon);
      preview.addEventListener("click", () => void this._play(item));
      wrapper.append(preview);
    }

    const label = document.createElement("div");
    label.className = "video-title";
    label.textContent = item.title;
    wrapper.append(label);
    return wrapper;
  }

  _state(message, error = false) {
    const state = document.createElement("div");
    state.className = error ? "state error" : "state";
    state.textContent = message;
    return state;
  }

  _text(key, count = 0) {
    const language = this._hass?.locale?.language || this._hass?.language || "en";
    const zh = String(language).toLowerCase().startsWith("zh");
    const messages = zh
      ? {
          title: "今日门铃视频",
          count: `${count} 个视频`,
          refresh: "刷新",
          loading: "正在加载...",
          empty: "今天暂无归档视频",
          error: "视频列表加载失败",
          play: "播放视频",
        }
      : {
          title: "Today's Doorbell Videos",
          count: `${count} videos`,
          refresh: "Refresh",
          loading: "Loading...",
          empty: "No archived videos today",
          error: "Failed to load videos",
          play: "Play video",
        };
    return messages[key];
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, XiaomiDoorbellArchiveCard);
}
if (!customElements.get(`${CARD_TAG}-editor`)) {
  customElements.define(`${CARD_TAG}-editor`, XiaomiDoorbellArchiveCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Xiaomi Doorbell Archive",
    description: "Today's archived doorbell videos",
    preview: true,
  });
}
