# YC Demo Video Creation Guide

This guide explains how to create the professional YC demo video (`yc_demo_recording.webm`) with proper timing, smart editing, and Groq-powered fast responses.

## Quick Start

### Option 1: Python Script (Recommended)

**Step 1:** Start the demo server with Groq
```bash
# In terminal 1:
cd /Users/deeprajjha/Downloads/agent-manager
GROQ_API_KEY=your_groq_key_here make demo
```

**Step 2:** Run the demo script
```bash
# In terminal 2:
python3 scripts/create_yc_demo.py
```

**Step 3:** Record with screen capture
- Use QuickTime Player → File → New Screen Recording
- Or use OBS Studio for more control
- Record the terminal running `create_yc_demo.py`
- Save as `canvases/yc_demo_recording.webm`

### Option 2: Automated Recording (Requires asciinema)

**Step 1:** Install dependencies
```bash
brew install asciinema ffmpeg
cargo install agg  # OR download from https://github.com/asciinema/agg/releases
```

**Step 2:** Start demo server
```bash
# In terminal 1:
GROQ_API_KEY=your_groq_key_here make demo
```

**Step 3:** Run the automated script
```bash
# In terminal 2:
./scripts/create_yc_demo_video.sh
```

This will automatically:
1. Record the terminal session
2. Convert to video format
3. Save to `canvases/yc_demo_recording.webm`

## What the Demo Shows

The demo is structured to tell a compelling story in ~2 minutes:

### 1. Opening (4 seconds)
- Title card: "PlanMyAgents - Underwriters Laboratories for AI Tools"

### 2. Problem Statement (9 seconds)
- Shows documented failures in the AI tool ecosystem
- 72% undocumented parameters, 200K servers at risk
- No way to verify which tools work

### 3. Solution Overview (6 seconds)
- 4-step process: Discover → Test → Verify → Route
- Explains the automated pipeline

### 4. Live Demo (10 seconds)
- **Smart editing**: Shows the goal request immediately
- **Fast response**: Groq makes the planning instant
- **Clear result**: Shows verified tool found with credibility score
- **No boring waits**: Cuts directly to results

### 5. Differentiation (5.5 seconds)
- Compares to LangSmith, Datadog, Arize
- Explains independent third-party verification
- Underwriters Laboratories analogy

### 6. Market Validation (4 seconds)
- 97M monthly downloads, 41% org adoption
- Snyk acquisition validates category

### 7. Roadmap (5.5 seconds)
- AI-powered test generation
- Self-service verification
- Partnership distribution

### 8. Closing (4 seconds)
- Final title card with website

**Total Duration:** ~2 minutes

## Key Features

### Smart Editing
- **No long waits**: The demo shows "Searching..." for 1.5 seconds, then immediately shows results
- **Groq acceleration**: With Groq API key, the actual planning takes <2 seconds
- **Clear sections**: Each section has a title card that stays for 3-4 seconds

### Professional Presentation
- **Consistent timing**: All intro cards stay for 3-4 seconds (readable but not boring)
- **Color coding**: Uses terminal colors to highlight key information
- **Clear structure**: Problem → Solution → Demo → Differentiation → Next Steps

### Honest Demo
- **Real API calls**: Uses the actual `/goal` endpoint
- **Graceful fallback**: Shows mock data if server isn't running
- **No fake claims**: Only shows what actually works

## Customization

### Adjust Timing
Edit `scripts/create_yc_demo.py` and change the `duration` parameters:

```python
self.print_title("Title", duration=4.0)  # Title cards
self.print_section("Section", "Content", duration=3.5)  # Sections
```

### Change Demo Goal
Edit the goal in `scripts/create_yc_demo.py`:

```python
goal = "Your custom goal here"
```

### Add More Sections
Add new sections in the `run_demo()` method:

```python
self.print_section(
    "NEW SECTION TITLE",
    "Content goes here\n"
    "Multiple lines supported",
    duration=3.0
)
```

## Troubleshooting

### Demo server not responding
**Problem:** Script shows "Demo server not responding"

**Solution:**
1. Make sure demo server is running: `make demo`
2. Check it's accessible: `curl http://127.0.0.1:8787`
3. The script will continue with mock data if server is down

### Groq responses are slow
**Problem:** Planning takes >10 seconds

**Solution:**
1. Check `GROQ_API_KEY` is set in `.env`
2. Verify key is valid: `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"`
3. Fallback: Script works without Groq, just slower

### Video quality issues
**Problem:** Text is blurry or hard to read

**Solution:**
1. Increase terminal font size before recording
2. Use a high-contrast color scheme
3. Record at higher resolution (1920x1080 minimum)
4. For automated recording, adjust `agg` parameters in the shell script:
   ```bash
   agg demo.cast demo.gif --font-size 18 --line-height 1.5
   ```

### Recording is too long/short
**Problem:** Demo doesn't fit in 2 minutes

**Solution:**
1. Adjust `duration` parameters in the Python script
2. Remove less critical sections
3. Speed up the video in post-processing:
   ```bash
   ffmpeg -i input.webm -filter:v "setpts=0.75*PTS" output.webm
   ```

## Manual Recording Tips

If you're recording manually with QuickTime or OBS:

1. **Prepare your terminal:**
   - Use a clean terminal with no distractions
   - Set font size to 14-16pt
   - Use a high-contrast theme (dark background, light text)
   - Maximize terminal to fill most of the screen

2. **Before recording:**
   - Close unnecessary applications
   - Turn off notifications
   - Test the demo script once to ensure smooth flow

3. **During recording:**
   - Let each title card stay for the full duration
   - Don't interrupt or stop the script
   - If you make a mistake, just re-record

4. **After recording:**
   - Trim any dead space at start/end
   - Export as WebM format (VP9 codec)
   - Target file size: 1-2 MB for YC upload

## Converting to Other Formats

### To MP4 (for broader compatibility):
```bash
ffmpeg -i canvases/yc_demo_recording.webm \
  -c:v libx264 -crf 23 -preset medium \
  canvases/yc_demo_recording.mp4
```

### To GIF (for embedding in docs):
```bash
ffmpeg -i canvases/yc_demo_recording.webm \
  -vf "fps=10,scale=800:-1:flags=lanczos" \
  canvases/yc_demo_recording.gif
```

### Compress for upload:
```bash
ffmpeg -i canvases/yc_demo_recording.webm \
  -c:v libvpx-vp9 -b:v 1M -crf 35 \
  canvases/yc_demo_recording_compressed.webm
```

## Best Practices

1. **Test first**: Run the demo script without recording to ensure everything works
2. **Use Groq**: Set `GROQ_API_KEY` for fast responses (makes the demo feel snappy)
3. **Keep it short**: YC reviewers watch hundreds of videos - 2 minutes is perfect
4. **Show, don't tell**: The live demo is more convincing than slides
5. **Be honest**: Only show what actually works (the script does this automatically)

## Questions?

If you encounter issues not covered here:
1. Check the script comments in `scripts/create_yc_demo.py`
2. Review the demo server logs: `make demo` output
3. Test the API manually: `curl http://127.0.0.1:8787/api/goal?goal=test`
