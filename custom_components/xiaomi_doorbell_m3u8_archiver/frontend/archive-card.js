const CARD_TAG = "xiaomi-doorbell-archive-card";
const MEDIA_SOURCE_ID = "media-source://xiaomi_doorbell_m3u8_archiver";

class XiaomiDoorbellArchiveCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._items = [];
    this._activeVideo = null;
    this._activeVideoUrl = null;
    this._loaded = false;
    this._loading = false;
    this._loadingVideo = null;
    this._error = null;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid card configuration");
    }

    const entryChanged = this._config.entry_id !== config.entry_id;
    this._config = { ...config };
    if (entryChanged && this._loaded) {
      this._loaded = false;
      this._items = [];
      this._activeVideo = null;
      this._activeVideoUrl = null;
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
    if (this._hass && !this._loaded && !this._loading) {
      void this._load();
    }
  }

  getCardSize() {
    return Math.max(2, Math.ceil(this._items.length / 2) * 3);
  }

  async _load() {
    if (!this._hass || this._loading) {
      return;
    }

    this._loading = true;
    this._error = null;
    this._render();
    try {
      const media = await this._hass.callWS({
        type: "media_source/browse_media",
        media_content_id: MEDIA_SOURCE_ID,
      });
      const children = Array.isArray(media.children) ? media.children : [];
      this._items = children.filter(
        (item) => item.can_play && this._matchesConfiguredEntry(item),
      );
      this._activeVideo = null;
      this._activeVideoUrl = null;
      this._loaded = true;
    } catch (error) {
      this._error = error instanceof Error ? error.message : String(error);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  _matchesConfiguredEntry(item) {
    if (!this._config.entry_id) {
      return true;
    }
    return item.media_content_id.includes(`/video/${this._config.entry_id}/`);
  }

  async _play(item) {
    const id = item.media_content_id;
    this._loadingVideo = id;
    this._error = null;
    this._render();
    let readyToPlay = false;
    try {
      const resolved = await this._hass.callWS({
        type: "media_source/resolve_media",
        media_content_id: id,
      });
      this._activeVideoUrl = resolved.url;
      readyToPlay = true;
    } catch (error) {
      this._error = error instanceof Error ? error.message : String(error);
    } finally {
      this._loadingVideo = null;
    }
    if (readyToPlay) {
      this._renderPlayer(id, true);
    } else {
      this._render();
    }
  }

  _renderPlayer(id, startPlayback) {
    this._activeVideo = id;
    this._render();
    if (!startPlayback) {
      return;
    }
    const video = this.shadowRoot.querySelector("video[data-active='true']");
    if (video) {
      void video.play().catch(() => undefined);
    }
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }

    const card = document.createElement("ha-card");
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
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
      }
      .video-item {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        min-width: 0;
        overflow: hidden;
      }
      .preview {
        align-items: center;
        aspect-ratio: 16 / 9;
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
        height: 56px;
        justify-content: center;
        position: absolute;
        width: 56px;
      }
      .play-icon ha-icon { --mdc-icon-size: 38px; }
      .preview:hover .play-icon { background: rgb(0 0 0 / 72%); color: #30b8b2; }
      video {
        aspect-ratio: 16 / 9;
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
      video.dataset.active = "true";
      video.addEventListener("play", () => this._pauseOtherVideos(video));
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

  _pauseOtherVideos(activeVideo) {
    for (const video of this.shadowRoot.querySelectorAll("video")) {
      if (video !== activeVideo) {
        video.pause();
      }
    }
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

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "Xiaomi Doorbell Archive",
    description: "Today's archived doorbell videos",
    preview: true,
  });
}
