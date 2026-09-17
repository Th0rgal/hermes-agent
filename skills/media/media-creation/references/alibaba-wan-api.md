# Alibaba Wan (DashScope) API Notes

Sources: Alibaba Cloud DashScope API references for Wan text-to-video, image-to-video, and image generation. Credentials redacted.

## Video Models

| Model | Type | Max Resolution | Duration | Use Case |
|-------|------|----------------|----------|----------|
| `wan2.6-t2v` | T2V | 1080P | 5-15s | Latest text-to-video, highest quality |
| `wan2.6-i2v` | I2V | 1080P | 5-15s | Latest image-to-video, highest quality |
| `wan2.5-t2v-preview` | T2V | 1080P | 5s | High quality, slightly older |
| `wan2.5-i2v-preview` | I2V | 1080P | 5s | High quality, slightly older |
| `wan2.2-i2v-plus` | I2V | 720P | 5s | Balanced quality/cost |
| `wan2.2-t2v-flash` | T2V | 480P | 5s | Fast/cheap |

## Image Models

| Model | Type | Resolution | Use Case |
|-------|------|------------|----------|
| `wan2.6-image` | T2I | Up to 1024x1024 | Latest, fewer restrictions |
| `wanx2.1-t2i-turbo` | T2I | Up to 1024x1024 | Fast image generation |
| `wanx2.1-t2i-plus` | T2I | Up to 1024x1024 | Higher quality |

## Auth and Base URL
- Base URL (Singapore): `https://dashscope-intl.aliyuncs.com/api/v1`
- Header: `Authorization: Bearer <encrypted>YOUR_DASHSCOPE_API_KEY</encrypted>` (placeholder; replaced at deploy time)
- Async header (for task-based flows): `X-DashScope-Async: enable`

## Video Generation (Wan 2.6)

Endpoint:
- `POST /services/aigc/video-generation/video-synthesis`

Text-to-video (T2V) request shape:
```json
{
  "model": "wan2.6-t2v",
  "input": {
    "prompt": "<scene and motion prompt>",
    "audio_url": "<optional audio URL>"
  },
  "parameters": {
    "size": "1280*720",
    "duration": 5,
    "prompt_extend": true
  }
}
```

Image-to-video (I2V) request shape:
```json
{
  "model": "wan2.6-i2v",
  "input": {
    "prompt": "<motion prompt>",
    "img_url": "data:image/jpeg;base64,<BASE64>",
    "audio_url": "<optional audio URL>"
  },
  "parameters": {
    "resolution": "1080P",
    "duration": 5,
    "prompt_extend": true,
    "shot_type": "multi"
  }
}
```

Notes:
- T2V uses `size` (e.g., `1280*720`); I2V uses `resolution` (720P/1080P).
- `duration` for Wan 2.6 is 5, 10, or 15 seconds.
- Multi-shot narrative is available via `shot_type: "multi"` when `prompt_extend` is true (Wan 2.6).

Task flow:
- Response returns `output.task_id` and `output.task_status`.
- Poll `GET /tasks/{task_id}` until `SUCCEEDED`, then use `output.video_url`.

## Image Generation (Wan 2.6)

Two HTTP options are documented:
1) **Sync/SSE**: `POST /services/aigc/multimodal-generation/generation`
2) **Async**: `POST /services/aigc/image-generation/generation` with `X-DashScope-Async: enable`

Request shape:
```json
{
  "model": "wan2.6-image",
  "input": {
    "messages": [
      {"role": "user", "content": [
        {"text": "<image prompt>"},
        {"image": "<optional URL or base64 data URI>"}
      ]}
    ]
  },
  "parameters": {
    "size": "1024*1024",
    "n": 1,
    "prompt_extend": true,
    "watermark": false
  }
}
```

Async response returns a task ID; poll `GET /tasks/{task_id}` for result URLs.

## Legacy Image Generation (Wanx Models)

For `wanx2.1-t2i-turbo` and `wanx2.1-t2i-plus`:

Endpoint: `POST /services/aigc/text2image/image-synthesis`

```json
{
  "model": "wanx2.1-t2i-turbo",
  "input": {
    "prompt": "A majestic lion in the African savanna"
  },
  "parameters": {
    "size": "1280*720",
    "n": 1,
    "style": "<photography>"
  }
}
```

### Style Options
`"<auto>"`, `"<photography>"`, `"<portrait>"`, `"<3d cartoon>"`, `"<anime>"`, `"<oil painting>"`, `"<watercolor>"`, `"<sketch>"`, `"<chinese painting>"`, `"<flat illustration>"`

## Input Requirements (image fields)
- `image` or `img_url` can be a public URL or base64 data URI
- Data URI format: `data:{MIME_type};base64,{base64_data}`

## Security
- Never store API keys in code
- Use `<encrypted>YOUR_DASHSCOPE_API_KEY</encrypted>` placeholder (encrypted at rest during skill sync)
