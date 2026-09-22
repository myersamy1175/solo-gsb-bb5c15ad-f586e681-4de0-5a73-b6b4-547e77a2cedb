# 字幕时间轴修复 API

基于 **Python + FastAPI + Pydantic** 的字幕时间轴修复服务。

接收顺序固定的字幕片段（毫秒级起止时间）、最小间隔与不可移动（锁定）片段，
在**保持文本、时长和锁定位置不变**的前提下，生成**无重叠**且**总绝对位移最小**
的新时间轴。当锁定约束导致无解时，返回发生冲突的片段及具体原因。

## 一条命令启动

```bash
docker compose up --build
```

启动后访问 <http://localhost:8000>（自动跳转到交互式 API 文档 `/docs`）。

停止服务：

```bash
docker compose down
```

## 必要配置

- **端口**：默认容器对外暴露 `8000`，无需任何环境变量即可运行。
  如需改到其他宿主机端口，启动时设置 `PORT`：

  ```bash
  PORT=9000 docker compose up --build
  # 然后访问 http://localhost:9000
  ```

- 容器在**构建镜像时安装全部依赖**（见 `Dockerfile`，依赖清单见
  `requirements.txt`），运行时无需联网。

本地（不使用 Docker）开发运行：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

### `GET /health`

健康检查，返回：

```json
{"status": "ok"}
```

### `POST /normalize`

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `segments` | 数组（至少 1 条） | 字幕片段，**按播放顺序排列且顺序固定** |
| `segments[].id` | 字符串，可选 | 片段标识，缺省时使用下标（`"0"`, `"1"`, …） |
| `segments[].start_ms` | 整数 ≥ 0 | 起始时间（毫秒） |
| `segments[].end_ms` | 整数 | 结束时间（毫秒），必须大于 `start_ms` |
| `segments[].text` | 字符串 | 字幕文本，原样保留 |
| `segments[].locked` | 布尔，默认 `false` | 为 `true` 时该片段不可移动 |
| `min_gap_ms` | 整数 ≥ 0，默认 `0` | 相邻片段之间要求的最小间隔（毫秒） |

示例：

```bash
curl -X POST http://localhost:8000/normalize \
  -H 'Content-Type: application/json' \
  -d '{
    "segments": [
      {"id": "a", "start_ms": 0,    "end_ms": 800,  "text": "你好"},
      {"id": "b", "start_ms": 900,  "end_ms": 1500, "text": "世界", "locked": true},
      {"id": "c", "start_ms": 1400, "end_ms": 1600, "text": "！"}
    ],
    "min_gap_ms": 50
  }'
```

成功响应（`200`）：

```json
{
  "status": "ok",
  "segments": [
    {"id": "a", "start_ms": 0,    "end_ms": 800,  "text": "你好", "locked": false, "shift_ms": 0},
    {"id": "b", "start_ms": 900,  "end_ms": 1500, "text": "世界", "locked": true,  "shift_ms": 0},
    {"id": "c", "start_ms": 1550, "end_ms": 1750, "text": "！",   "locked": false, "shift_ms": 150}
  ],
  "min_gap_ms": 50,
  "total_abs_shift_ms": 150
}
```

`shift_ms` 为该片段起点的位移量（可为负）；`total_abs_shift_ms` 为所有片段
位移绝对值之和，即被最小化的目标函数。片段的**时长**（`end_ms - start_ms`）与
**文本**始终保持不变；`locked: true` 的片段位置严格不变。

#### 约束无解（`409`）

当两个锁定片段之间放不下中间内容（时长合计 + 间隔）时，约束无解，返回
`409 Conflict` 并指出每一对冲突片段、所需/可用跨度和原因：

```json
{
  "status": "conflict",
  "message": "No overlap-free timeline exists while keeping every locked cue in place. See conflicts for details.",
  "conflicts": [
    {
      "first_id": "a",
      "second_id": "b",
      "first_position_ms": 0,
      "second_position_ms": 800,
      "required_ms": 1050,
      "available_ms": 800,
      "shortfall_ms": 250,
      "reason": "Locked cue 'b' at 800 ms is too close to 'a' at 0 ms: ..."
    }
  ]
}
```

`first_id` 为 `@start` 表示锁定片段距离时间轴原点（0 ms）太近，无法把它之前
的片段排在非负时间上。请求参数本身不合法（负时间、`end_ms <= start_ms`、
负间隔、空片段列表等）返回 `422` 校验错误。

## 算法说明

保持每条字幕时长不变、只移动起点 `x_i`，相邻约束为
`x_i + d_i + g ≤ x_{i+1}`，锁定片段满足 `x_i = start_i`。对起点做保序变换
`y_i = x_i − (start_0 + i·g + Σd)` 后，链式约束化为简单保序约束
`y_0 ≤ y_1 ≤ …`，锁定点变为固定值，目标 `Σ|x_i − start_i|` 化为
**带固定值约束的 L1 保序回归**。

锁定点将序列切分为互相独立的区间，每个区间是以两侧锁定值为界的 L1 保序回归，
使用**中位数 PAVA（Pool Adjacent Violators Algorithm）**精确求解，块堆采用
小并大合并，复杂度约 O(n log² n)，纯 Python 实现、无重型数值依赖。
50 万条字幕级别的时间轴可在数秒内完成。

## 项目结构

```
app/
  main.py        # FastAPI 路由（/health、/normalize）
  schemas.py     # Pydantic 请求/响应模型与参数校验
  optimizer.py   # L1 保序回归求解器（中位数 PAVA）
tests/           # pytest 测试（含对小规模随机用例的穷举对照）
Dockerfile       # 镜像构建时安装依赖
docker-compose.yml
requirements.txt
```

运行测试：

```bash
pip install -r requirements-dev.txt
pytest -q
```
