<p align="center">
  <img src="icon.png" width="128" height="128" alt="Xiaomi Doorbell M3U8 Archiver logo">
</p>

## Xiaomi Doorbell M3U8 Archiver

Custom Home Assistant integration for Xiaomi doorbell video event pipeline.

The integration includes local branding in `brand/icon.png` and `brand/logo.png`,
with 512px `@2x` variants, for Home Assistant versions supporting local custom
integration brand assets. Keep the `brand/` directory when installing the integration.

中文文档：`README.zh-CN.md`

### Development

Set up a local Python environment and install test/lint tools:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip pytest pytest-asyncio ruff homeassistant
```

Or use the mise/uv task setup:

```bash
mise trust
mise run setup
mise run build
mise run test:unit
mise run test:integration
```

Common development commands:

```bash
# format
python -m ruff format .

# lint
python -m ruff check .

# lint with autofix
python -m ruff check . --fix

# syntax/import smoke check
python -m compileall custom_components tests

# full tests
python -m pytest -q

# single test file
python -m pytest -q tests/test_orchestrator_happy_path.py

# single test by node id
python -m pytest -q tests/test_orchestrator_happy_path.py::test_orchestrator_happy_path
```

<!-- ha-marketplace:exclude-start -->

### Install

#### Marketplace (Home Assistant 2026.11 or later)

1. Go to **Settings -> Marketplace -> Add from link** and connect GitHub when prompted.
2. Enter `https://github.com/iavjkae/xiaomi-doorbell-m3u8-archiver` and select **Add**.
   If asked for a type, select **Integration**.
3. Open **Xiaomi Doorbell M3U8 Archiver** and select **Install**.
4. Follow Home Assistant's setup or restart prompt, then configure the integration below.

#### HACS

1. Add `https://github.com/iavjkae/xiaomi-doorbell-m3u8-archiver` as a custom
   repository in HACS, with type **Integration**.
2. Download **Xiaomi Doorbell M3U8 Archiver** and restart Home Assistant.

#### Manual installation

1. Copy `custom_components/xiaomi_doorbell_m3u8_archiver` into
   `<HA config>/custom_components/`, keeping the complete integration folder.
2. Restart Home Assistant.

<!-- ha-marketplace:exclude-end -->

### Configuration

This integration depends on `xiaomi_miot` to provide doorbell stream metadata.

#### Prerequisites

1. Install and configure `xiaomi_miot` first.
2. Ensure your doorbell/camera entity is available in Home Assistant.
3. Verify the entity exposes:
   - `stream_address` (required): m3u8 stream URL
   - `motion_video_time` (recommended): motion event timestamp

If `stream_address` is missing, this integration cannot enqueue jobs.

#### Setup Steps

This integration is configured from Home Assistant UI:

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **Xiaomi Doorbell M3U8 Archiver**.
3. Select your Xiaomi Miot doorbell/camera entity in `camera_entity_id`.
4. Optionally adjust runtime settings:
   - `storage_root` (default `/media/xiaomi_video`)
   - `ffmpeg_path` (default `ffmpeg`)
   - `fetch_concurrency` (`1-4`, default `2`)
   - `process_concurrency` (currently fixed to `1`)
   - `max_queue_size` (default `200`)
   - `retry_limit` (default `1`)
   - `retry_backoff_sec` (default `0.2`)

Configuration is stored in Home Assistant Config Entries (not in `configuration.yaml`).

#### Event Triggering Model

- The integration listens to state updates of the configured `camera_entity_id`.
- A new job is enqueued when `motion_video_time` changes.
- Stream URL is resolved from `stream_address` on that same entity.
- Manual enqueue is also available through service calls.

#### Quick Validation

After setup, validate:

1. Sensor and binary sensor entities are created.
2. New motion events change queue/process counters.
3. Videos are written to `storage_root/YYYY/MM/` with entity-prefixed filenames.

### Integration Overview and Videos

Each config entry creates one archive device in Home Assistant. Open the integration and
select that device to see the runtime counters, queue and job status entities together.

The integration also provides one stable media source named **Today's Doorbell Videos**.
Open Home Assistant's Media browser and select it to list every MP4 archived
today. The file list is generated when the source is opened, without creating one entity
per file or periodically polling the filesystem. Select a video to start playback.

### Dashboard Card

After restarting Home Assistant, add the **Xiaomi Doorbell Archive** custom card from the
dashboard card picker. It loads today's list once when the card opens and resolves a video
only after it is selected. Video covers are generated on demand from representative frames
and cached for the current HA runtime. Use the refresh button to rescan manually.

The equivalent YAML configuration is:

```yaml
type: custom:xiaomi-doorbell-archive-card
title: Today's doorbell videos
columns: 0
min_video_width: 260
aspect_ratio: "16:9"
# entry_id: optional-config-entry-id
```

Without `entry_id`, the card shows today's videos from all configured archive entries.
The visual editor's archive instance picker lists this integration's entries by name;
clear the selection to show all instances. No manual ID lookup is needed.
`entry_id` is not a camera entity ID.

The visual card editor exposes layout settings. Set `columns` to 1-6 videos per row,
or `0` for automatic layout. Automatic layout uses `min_video_width` (120-640 pixels,
default 260) and falls back to one column in narrow cards. Fixed columns share the
available width equally. `aspect_ratio` accepts `16:9`, `4:3`, or `1:1` for both
previews and players; playback preserves the video's original proportions.

### Services

- `xiaomi_doorbell_m3u8_archiver.enqueue`
- `xiaomi_doorbell_m3u8_archiver.get_status`

`enqueue` service parameters:

- `entity_id` (required): camera or doorbell entity id
- `event_time` (optional): manual event timestamp override

`enqueue` still requires the target entity to expose `stream_address`.

`get_status` supports optional routing parameters:

- `entry_id` (optional): target one config entry explicitly
- `entity_id` (optional): target runtime by configured camera entity

When multiple integration entries exist and `get_status` is called without a target,
the service returns aggregated multi-instance status.

Users do not need to configure or provide `stream_url`. The integration resolves stream metadata from the configured entity state.

`storage_root` and `ffmpeg_path` are optional and use defaults shown above.

Storage layout uses monthly folders and timestamp filenames:

- Folder: `storage_root/YYYY/MM/`
- File: `<entity_id>_YYYY_MM_DD_HH_mm_ss.mp4`, for example
  `camera.front__door_2026_10_19_10_00_00.mp4`. Unsafe filename characters are
  percent-encoded; dots and repeated underscores are preserved.
- Same-second conflicts receive `_01`, `_02` suffixes. Files are copied into a
  temporary location before being published under an exclusively reserved name.
- Legacy sanitized prefixes remain readable when configured cameras give them a
  single owner. If multiple camera IDs collapse to the same old prefix, those
  legacy files are omitted from the media source and left untouched on disk.
  After confirming ownership, rename the prefix to the complete camera entity ID
  to make an ambiguous legacy file available again.

### Sensors

- `sensor.xiaomi_doorbell_m3u8_archiver_total_done`
- `sensor.xiaomi_doorbell_m3u8_archiver_total_failed`
- `sensor.xiaomi_doorbell_m3u8_archiver_processed_today`
- `sensor.xiaomi_doorbell_m3u8_archiver_queue_fetch_size`
- `sensor.xiaomi_doorbell_m3u8_archiver_queue_process_size`
- `sensor.xiaomi_doorbell_m3u8_archiver_last_job_status`
- `sensor.xiaomi_doorbell_m3u8_archiver_current_job_status`
- `binary_sensor.xiaomi_doorbell_m3u8_archiver_is_busy`

`is_busy` includes queued jobs, running jobs and resource cleanup, not just queue
depth. `current_job_status` reports the oldest unfinished admitted job, falling
back to `last_job_status` when none remain. `last_job_status` is the status of the
most recently updated job.

Counters reset when the integration reloads. `processed_today` follows Home
Assistant's configured timezone and resets on the next read after midnight.
Memory retains active jobs and at most 256 completed or failed jobs; evicting old
records does not change the counters or delete archived videos.

Unloading stops admission and drains downloads before shutting down processing.
Cancelling a shutdown waiter does not cancel the shared shutdown task; later
waiters can await the same cleanup.
Download and processing failures use the configured retry policy. Storage writes
are not automatically repeated because a failed write may already have committed.
Retention maintenance failures are logged without retrying or failing a saved video.
