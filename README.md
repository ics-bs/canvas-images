# Canvas Course Banner Prompts

This repository stores copy/paste-ready prompts for generating Canvas course banners with image models such as ChatGPT or DALL-E.

Each prompt file should contain one complete prompt that can be pasted directly into a web GUI. Do not require the user to combine multiple files at generation time.

## Structure

- `base/base-prompt.md` - source prompt text used when creating complete prompts
- `base/color-guide.md` - source color guide and course grouping outline
- `base/design-guide.md` - source design instruction guide for each course and subcourse
- `templates/prompt-template.md` - shared template for both courses and subcourses
- `prompts/` - final copy/paste-ready course and subcourse prompts
- `prompts/bachelors-programme/` - semester-based bachelor's programme prompt families
- `prompts/freestanding-courses-muted-crimson/` - freestanding course prompts
- `prompts/one-year-masters-programme-copper/` - one-year master's programme prompts
- `prompts/doctoral-courses-teal-forest/` - doctoral course prompts
- `outputs/README.md` - notes for generated images, settings, and iterations
- `scripts/generate-images.py` - batch image generator for the OpenAI Image API

## Prompt Rule

Course prompts and subcourse prompts use the same template. Only two parts should normally differ:

- `Color instruction` - the assigned banner family palette
- `Design instruction` - the specific visual concept, shapes, objects, or blueprint elements for that course or subcourse

The final prompt must still include the common Canvas banner format, academic blueprint style, composition constraints, and negative instructions so it remains easy to copy and paste.

Folder names encode the programme or color family for navigation, but each final prompt file includes the color instruction directly.

## Base vs Final Prompts

The `base/` files are the source material for maintaining consistent prompt language and color families. They are not meant to be pasted together manually each time.

The `prompts/` files are the final outputs. Each one should include the relevant base wording, color instruction, and design instruction in one complete copy/paste prompt.

## Generate Images

Install dependencies into the local virtual environment:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Preview the planned output paths without API calls:

```bash
.venv/bin/python scripts/generate-images.py --dry-run --limit 3
```

Generate one prompt:

```bash
OPENAI_API_KEY=... .venv/bin/python scripts/generate-images.py --only software-development --limit 1
```

By default the script:

- uses `gpt-image-2`
- generates a `1920x640` banner
- derives a `1600x896` thumbnail from the generated banner
- saves files under the matching `outputs/` folder
- skips existing output files unless `--overwrite` is passed
