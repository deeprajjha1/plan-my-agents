# Repo Canvases

These `.canvas.tsx` files are repo-owned mirrors of planning and audit canvases
created during product review. Cursor's live canvas runtime uses its managed
workspace canvas directory, but keeping these files here makes the artifacts
reviewable and versionable with the rest of the product docs.

Current canvases:

- `honest-scope-existing-vs-new.canvas.tsx`
- `planmyagents-product-audit.canvas.tsx`
- `product-architecture-audit.canvas.tsx`

## YC Demo Video

- `yc_demo_recording.webm` - Professional demo video for YC W26 application
- `yc_application_revisions.md` - YC application Q&A with strategic alignment
- `YC_DEMO_README.md` - Guide for creating/updating the demo video

To recreate the demo video with proper timing and Groq-powered fast responses:

```bash
# Start demo server with Groq
GROQ_API_KEY=your_key make demo

# In another terminal, run the demo script
python3 scripts/create_yc_demo.py

# Or use automated recording (requires asciinema + agg)
./scripts/create_yc_demo_video.sh
```

See `YC_DEMO_README.md` for detailed instructions.
