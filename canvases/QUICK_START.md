# YC Demo Video - Quick Start

## TL;DR

```bash
# Terminal 1
GROQ_API_KEY=your_groq_key_here make demo

# Terminal 2
python3 scripts/create_yc_demo.py

# Record with QuickTime/OBS → Save as canvases/yc_demo_recording.webm
```

## What You Get

- ✅ 2-minute professional demo
- ✅ Smart editing (no boring waits)
- ✅ Groq-powered fast responses
- ✅ Proper 3-4 second intro screens
- ✅ Clear problem → solution → demo flow

## Files

- **Run**: `scripts/create_yc_demo.py`
- **Output**: `canvases/yc_demo_recording.webm`
- **Docs**: `canvases/YC_DEMO_README.md`

## Demo Flow (2 min)

1. Opening (4s)
2. Problem (9s) - 72% undocumented, 200K at risk
3. Solution (6s) - Discover → Test → Verify → Route
4. **Live Demo (10s)** - Shows verified tool found ⚡
5. Differentiation (5.5s) - vs LangSmith/Datadog
6. Market (4s) - 97M downloads, Snyk acquisition
7. Roadmap (5.5s) - AI test generation
8. Closing (4s)

## Key Features

### Smart Editing ⚡
- Shows "Searching..." for 1.5s
- Then **immediately** shows results
- No 30-60s boring wait time

### Groq Integration 🚀
- With GROQ_API_KEY: <2s responses
- Without: Still works, just slower
- Set in `.env` file

### Professional Polish ✨
- Color-coded sections
- Perfect timing (3-4s per screen)
- Honest demo (only shows what works)

## Troubleshooting

**Server not running?**
```bash
make demo
```

**No Groq key?**
- Demo works without it (just slower)
- Get key: https://console.groq.com

**Want to customize?**
- Edit `scripts/create_yc_demo.py`
- Change `duration` parameters
- Modify content in `run_demo()` method

## Full Docs

See `YC_DEMO_README.md` for:
- Detailed instructions
- Automated recording
- Video conversion
- Troubleshooting
- Customization guide
