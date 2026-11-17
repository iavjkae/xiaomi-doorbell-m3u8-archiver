<p align="center">
  <img src="icon.png" width="128" height="128" alt="小米门铃 M3U8 归档器 Logo">
</p>

## Xiaomi Doorbell M3U8 Archiver

用于 Home Assistant 的小米门铃视频事件处理自定义集成。

集成包含本地品牌图 `brand/icon.png` 和 `brand/logo.png`，以及 512px 的 `@2x`
版本，供支持自定义集成本地品牌资源的 Home Assistant 使用。安装时请保留集成
目录中的 `brand/` 文件夹。

English docs: `README.md`

### 开发环境

创建本地 Python 环境并安装测试/检查工具：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip pytest pytest-asyncio ruff homeassistant
```

也可以使用 mise/uv 任务：

```bash
mise trust
mise run setup
mise run build
mise run test:unit
mise run test:integration
```

常用开发命令：

```bash
# 格式化
python -m ruff format .

# 静态检查
python -m ruff check .

# 自动修复可修复问题
python -m ruff check . --fix

# 语法/导入快速检查
python -m compileall custom_components tests

# 全量测试
python -m pytest -q

# 单文件测试
python -m pytest -q tests/test_orchestrator_happy_path.py

# 单用例测试
python -m pytest -q tests/test_orchestrator_happy_path.py::test_orchestrator_happy_path
```

<!-- ha-marketplace:exclude-start -->

### 安装

#### Marketplace（Home Assistant 2026.11 及以上）

1. 进入 **Settings -> Marketplace -> Add from link**，按提示连接 GitHub 账号。
2. 输入 `https://github.com/iavjkae/xiaomi-doorbell-m3u8-archiver` 并选择 **Add**。
   如果提示选择类型，选择 **Integration**。
3. 打开 **Xiaomi Doorbell M3U8 Archiver** 并选择 **Install**。
4. 按 Home Assistant 提示完成配置或重启，再按下方说明配置集成。

#### HACS

1. 在 HACS 中添加自定义仓库 `https://github.com/iavjkae/xiaomi-doorbell-m3u8-archiver`，
   类型选择 **Integration**。
2. 下载 **Xiaomi Doorbell M3U8 Archiver**，然后重启 Home Assistant。

#### 手动安装

1. 将 `custom_components/xiaomi_doorbell_m3u8_archiver` 复制到
   `<HA 配置目录>/custom_components/` 下，保留完整的集成文件夹。
2. 重启 Home Assistant。

<!-- ha-marketplace:exclude-end -->

### 配置

本集成依赖 `xiaomi_miot` 提供门铃视频流元数据。

#### 前置条件

1. 先安装并配置 `xiaomi_miot`。
2. 确认门铃/摄像头实体已在 Home Assistant 中可用。
3. 确认该实体包含以下属性：
   - `stream_address`（必需）：m3u8 流地址
   - `motion_video_time`（建议）：门铃事件时间

如果缺少 `stream_address`，本集成无法入队任务。

#### 配置步骤

本集成通过 Home Assistant UI 配置：

1. 进入 **Settings -> Devices & Services -> Add Integration**。
2. 搜索 **Xiaomi Doorbell M3U8 Archiver**。
3. 在 `camera_entity_id` 中选择对应的 Xiaomi Miot 门铃/摄像头实体。
4. 可选运行参数：
   - `storage_root`（默认 `/media/xiaomi_video`）
   - `ffmpeg_path`（默认 `ffmpeg`）
   - `fetch_concurrency`（`1-4`，默认 `2`）
   - `process_concurrency`（当前固定为 `1`）
   - `max_queue_size`（默认 `200`）
   - `retry_limit`（默认 `1`）
   - `retry_backoff_sec`（默认 `0.2`）

配置保存于 Config Entries（不使用 `configuration.yaml`）。

#### 事件触发模型

- 集成会监听配置的 `camera_entity_id` 状态变化。
- 当 `motion_video_time` 变化时入队新任务。
- 流地址从同一实体的 `stream_address` 获取。
- 也支持通过服务手动触发入队。

#### 快速验证

配置后可按以下方式验证：

1. 传感器与二值传感器实体已创建。
2. 新门铃事件到来时，队列/处理计数会变化。
3. 视频写入 `storage_root/YYYY/MM/`，文件名带实体前缀。

### 集成概览与视频

每个配置项会在 Home Assistant 中创建一个归档器设备。进入本集成并打开该设备，
即可集中查看运行计数、队列状态和任务状态等实体。

集成还会提供一个名为 **今日门铃视频** 的稳定媒体源。在 Home
Assistant 的媒体浏览器中打开该媒体源，即可列出今天归档的全部 MP4。文件列表只
在打开媒体源时动态生成，不会为每个文件创建实体，也不会定时轮询文件系统；选择
某个视频后才会开始播放。

### Dashboard 卡片

重启 Home Assistant 后，可以直接从 Dashboard 的卡片选择器添加 **Xiaomi
Doorbell Archive** 自定义卡片。卡片打开时加载一次当天列表，只有选择某个视频后
才解析并播放。视频封面会按需从代表帧生成，并在本次 HA 运行期间缓存；需要重新
扫描时可点击刷新按钮。

对应的 YAML 配置如下：

```yaml
type: custom:xiaomi-doorbell-archive-card
title: 今日门铃视频
columns: 0
min_video_width: 260
aspect_ratio: "16:9"
# entry_id: 可选的配置项 ID
```

不填写 `entry_id` 时，卡片会显示所有归档配置项今天的视频。
可视化编辑器中的“归档实例”按名称选择本集成的配置实例，无需手动查找 ID；
清空选择即可显示全部实例。`entry_id` 不是摄像头实体 ID。

可在卡片的可视化编辑器中调整布局：`columns` 为每行视频数（1-6），
`0` 表示自动布局；自动布局使用 `min_video_width`（120-640 像素，默认 260）
决定每行数量，窄卡片仍会缩为一列。固定列数时，每列均分卡片宽度。
`aspect_ratio` 支持 `16:9`、`4:3` 和 `1:1`；封面与播放器使用相同比例，
播放画面保持原始比例，不会拉伸。

### 服务

- `xiaomi_doorbell_m3u8_archiver.enqueue`
- `xiaomi_doorbell_m3u8_archiver.get_status`

`enqueue` 服务参数：

- `entity_id`（必填）：摄像头或门铃实体 ID
- `event_time`（可选）：手动覆盖事件时间

`enqueue` 仍要求目标实体包含 `stream_address`。

`get_status` 支持可选路由参数：

- `entry_id`（可选）：显式指定某个 config entry
- `entity_id`（可选）：按配置的 camera entity 定位 runtime

当存在多实例且 `get_status` 未指定目标时，会返回多实例聚合结果。

用户无需手动配置 `stream_url`。集成会从配置实体状态中解析流信息。

`storage_root` 与 `ffmpeg_path` 均为可选项，使用上面的默认值即可。

存储目录按月组织，文件名按时间戳：

- 目录：`storage_root/YYYY/MM/`
- 文件：`<entity_prefix>_YYYY_MM_DD_HH_mm_ss.mp4`（同秒冲突追加 `_01`、`_02`）

新归档文件名为 `<entity_id>_YYYY_MM_DD_HH_mm_ss.mp4`，例如
`camera.front__door_2026_10_19_10_00_00.mp4`。点号和连续下划线保持不变，
不适合文件名的字符使用百分号编码。同秒重名时添加 `_01`、`_02` 后缀；
先完成临时复制，再通过排他保留的文件名发布，避免并发覆盖或显示半成品。

旧版清理过的文件名前缀在来源无冲突时继续兼容读取。若多个已配置摄像头映射到
同一个旧前缀，这些历史文件暂不列入媒体源，但保留在磁盘上，不猜测归属。
确认来源后，可将文件名前缀改为完整摄像头实体 ID，使其重新出现在媒体源中。

### 传感器

- `sensor.xiaomi_doorbell_m3u8_archiver_total_done`
- `sensor.xiaomi_doorbell_m3u8_archiver_total_failed`
- `sensor.xiaomi_doorbell_m3u8_archiver_processed_today`
- `sensor.xiaomi_doorbell_m3u8_archiver_queue_fetch_size`
- `sensor.xiaomi_doorbell_m3u8_archiver_queue_process_size`
- `sensor.xiaomi_doorbell_m3u8_archiver_last_job_status`
- `sensor.xiaomi_doorbell_m3u8_archiver_current_job_status`
- `binary_sensor.xiaomi_doorbell_m3u8_archiver_is_busy`

`is_busy` 包含排队、执行和资源清理中的任务，不只取决于队列长度。
`current_job_status` 表示最早接收且尚未结束的任务状态；没有此类任务时，
回退到 `last_job_status`，后者表示最近一次更新的任务状态。

计数在集成重载后重置。`processed_today` 使用 Home Assistant 配置的时区，
跨天后下一次读取即归零。内存保留活动任务及最多 256 条已完成或失败的任务记录；
淘汰旧记录不会影响累计计数，也不会删除归档视频。

卸载时先停止接收任务，排空下载阶段后再结束处理阶段。下载和视频处理失败使用配置的
重试策略；保存失败不会自动重复写入，以免实际已经保存而产生重复视频。
历史保留清理失败仅记录警告，不会重试或判定已保存的视频失败。
取消某次关闭等待不会取消共享的关闭任务；之后仍可等待同一清理流程完成。
