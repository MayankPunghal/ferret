# Plan: images and local language models (deferred)

**Status (2026-10-23): on hold, by decision of the owner.** Ingestion stays fast and uses one model only,
the embedding model. Text from PDF, Word, PowerPoint, HTML, Markdown and plain text is the focus. This page
records what was measured, so the work can resume without repeating it.

## Why it is on hold

Every use of a generative model costs seconds per item on a CPU, so in the ingest path it turns minutes
into hours. Measured on this 4-core CPU without a GPU:

| Step | Cost | For 1,000 items |
|---|---|---|
| Gemma 4 E2B: a summary, a title and topics per page | ~12 s per page | ~3.3 h |
| Gemma 4 E2B: an answer written from the top 8 passages | median 15 s per question | (query time) |
| PaddleOCR-VL-1.6: reading the text in an image | 60–70 s per image on 2 cores; the 4-core time was not measured | ~17–20 h |
| Today's text ingest, for comparison | Kubernetes docs: 623 pages in 26 min (embedding is most of it) | ~42 min |

The archive target (about 40,000 multi-MB PDFs) is out of reach for model-based enrichment on a CPU. It
would need a GPU, and even then it should be a background job that runs after the text is already
searchable, never something that delays search.

## What was measured

- **Answers written by Gemma 4 E2B** (`eval/llm_answers.py`, `eval/results/answers-gemma-4-E2B*.json`):
  - **Kubernetes:** no false answers on the 16 questions that should be refused; it answered 61% of the
    rest; its first cited passage was right 61% of the time, against 95% for search's top result.
  - **Fixtures:** 3 false answers out of 40.
  - **Conclusion:** useful for *writing* an answer over the passages search found. It is not a better
    evidence check than the current gate, and not a better ranker.
- **Page summaries in the passage embeddings:** adding the first 300 characters of the page to each
  passage did not change retrieval on the Kubernetes test (top result right 96.4% with or without it).
  The run with Gemma-written summaries was still in progress when this page was written. Its result is in
  `eval/results/corpus-kubernetes-nodesc-gemma-4-e2b-*` once it finishes.
- **Reading text in images** (`eval/image_set.py`, `eval/image_ocr.py`): the trial was stopped after 4
  images. PaddleOCR-VL-1.6 read all 4 perfectly: 2 docs pages, the holiday-calendar table and the
  leave-application form, with every word found and nothing made up. This is too small a sample to
  choose a model; the other candidates were not run.
- **The site that prompted this:** in the owner's intranet copy, about 14 pages have no text of their own
  (holiday calendars, landing pages). The HRIS, Leave and Attendance manuals are mostly screenshots,
  so only their captions are searchable today.

## The plan, when this resumes

1. **Images in the crawl (cheap, no model):**
   - site2md saves content images next to the page and links each one where it appears
     (`![alt](org-chart.png)`).
   - It skips icons and logos: tiny images, and images that appear on most pages.
   - It currently blocks images to load pages faster, so it would fetch them separately with the same
     sign-in.
   - Ingest stores them as attachments of the page. Search shows them with the passage they sit in. Their
     alt text and captions are indexed as text.
2. **Reading images (optional, off by default):**
   - A background job sends each image, and each PDF page with little text (screenshots, scans), to a
     local reading model.
   - It stores the text in the page section where the image sits and records which model produced it.
   - It skips images it has already read (by content hash) and never blocks search; images become
     searchable when it gets to them.
   - It is recommended only with a GPU.
3. **Trust rules:**
   - Text read from an image is the image's own words, so it can be cited, labelled "read from image".
   - A description written by a model can be wrong: it may help *find* the image, but the result always
     shows the image itself as the evidence.
4. **Language models at query time only (optional):**
   - Write an answer from the passages search found, with every claim tied to a cited passage, after the
     evidence check has passed.
   - Not used for ranking or for deciding whether to answer (measured worse).
5. **Summaries, titles and tags at ingest:** only if the Gemma-summary test shows a measurable gain, and
   then as the same kind of background job as step 2.

## Candidate models (October 2026)

Reading text in images: **PaddleOCR-VL-1.6** (0.9B, Apache-2.0, official llama.cpp files, prompts
`OCR:` / `Table Recognition:`), **GLM-OCR** (0.9B, MIT, `ggml-org/GLM-OCR-GGUF`, prompt
`Text Recognition:`). Describing pictures, and possibly reading text too: **Qwen3.5-0.8B / 2B / 4B**
(Apache-2.0, vision built in, `unsloth/Qwen3.5-*-GGUF` + `mmproj-F16.gguf`), **Gemma 4 E2B**
(`unsloth/gemma-4-E2B-it-GGUF` + `mmproj-F16.gguf`). If a general model reads text nearly as well as the
specialists, one model covers everything (answers, summaries, images): one download for users.

## How to resume the trial

```bash
uv run --group eval --with playwright --with pillow python eval/image_set.py --chromium <chrome>
llama-server -m paddleocr-vl-1.6.gguf --mmproj mmproj-paddleocr-vl-1.6.gguf -c 16384 -t 4 --port 8082 \
    --temp 0 --jinja --chat-template-file paddleocr-vl-1.6.jinja
uv run --group eval python eval/image_ocr.py --name paddleocr-vl-1.6 --prompt "OCR:" --read-only
# Qwen3.5: add --chat-template-kwargs '{"enable_thinking":false}'; general models: drop --read-only
```

Lesson learned: llama.cpp threads busy-wait. When another job uses part of the CPU, set `-t` to the free
cores only: with 4 threads on 2 free cores the same image took 366 s instead of 61 s.
