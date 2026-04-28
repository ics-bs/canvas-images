#!/usr/bin/env python3
"""Generate Canvas banner and thumbnail images from prompt markdown files."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openai import OpenAI, OpenAIError


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BANNER_SIZE = "1920x640"
DEFAULT_THUMBNAIL_SIZE = "1600x896"
DEFAULT_QUALITY = "medium"
DEFAULT_FORMAT = "png"


@dataclass(frozen=True)
class PromptJob:
    prompt_path: Path
    relative_path: Path
    output_dir: Path
    slug: str
    prompt: str

    def banner_path(self, output_format: str) -> Path:
        return self.output_dir / f"{self.slug}-banner.{output_format}"

    def thumbnail_path(self, output_format: str) -> Path:
        return self.output_dir / f"{self.slug}-thumbnail.{output_format}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Canvas course banner and thumbnail images from prompts/ markdown files."
    )
    parser.add_argument("--prompts-dir", type=Path, default=Path("prompts"))
    parser.add_argument("--outputs-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--quality", default=DEFAULT_QUALITY, choices=("low", "medium", "high", "auto"))
    parser.add_argument("--output-format", default=DEFAULT_FORMAT, choices=("png", "jpeg", "webp"))
    parser.add_argument("--banner-size", default=DEFAULT_BANNER_SIZE)
    parser.add_argument("--thumbnail-size", default=DEFAULT_THUMBNAIL_SIZE)
    parser.add_argument(
        "--thumbnail-mode",
        default="edit",
        choices=("edit", "direct"),
        help="Use edit to derive the thumbnail from the banner, or direct to generate it from text only.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="TEXT",
        help="Only run prompts whose path contains TEXT. Can be passed more than once.",
    )
    parser.add_argument("--limit", type=int, help="Limit the number of matching prompt files processed.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing output images.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned work without calling the API.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop at the first failed image request.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per API request after the first attempt.")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="Initial retry delay in seconds.")
    parser.add_argument("--pause", type=float, default=0.0, help="Pause between prompt jobs in seconds.")
    parser.add_argument("--timeout", type=float, default=180.0, help="API timeout per request in seconds.")
    return parser.parse_args()


def parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid size '{value}', expected WIDTHxHEIGHT.") from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(f"Invalid size '{value}', dimensions must be positive.")
    return width, height


def validate_gpt_image_2_size(name: str, value: str) -> None:
    width, height = parse_size(value)
    if width % 16 != 0 or height % 16 != 0:
        raise ValueError(f"{name} size {value} is invalid: both dimensions must be multiples of 16.")
    if max(width, height) > 3840:
        raise ValueError(f"{name} size {value} is invalid: maximum edge length is 3840px.")
    if max(width, height) / min(width, height) > 3:
        raise ValueError(f"{name} size {value} is invalid: long edge to short edge ratio exceeds 3:1.")
    pixels = width * height
    if pixels < 655_360 or pixels > 8_294_400:
        raise ValueError(
            f"{name} size {value} is invalid: total pixels must be between 655,360 and 8,294,400."
        )


def validate_args(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1.")
    if args.retries < 0:
        raise ValueError("--retries must be 0 or greater.")
    if args.retry_delay < 0:
        raise ValueError("--retry-delay must be 0 or greater.")
    if args.pause < 0:
        raise ValueError("--pause must be 0 or greater.")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0.")

    if args.model == "gpt-image-2":
        validate_gpt_image_2_size("banner", args.banner_size)
        validate_gpt_image_2_size("thumbnail", args.thumbnail_size)


def extract_prompt(markdown: str) -> str:
    marker = "## Copy/Paste Prompt"
    if marker not in markdown:
        return markdown.strip()
    return markdown.split(marker, 1)[1].strip()


def discover_jobs(prompts_dir: Path, outputs_dir: Path, filters: Iterable[str]) -> list[PromptJob]:
    filters = [part.lower() for part in filters]
    jobs: list[PromptJob] = []

    for prompt_path in sorted(prompts_dir.rglob("*.md")):
        if prompt_path.name.lower() == "readme.md":
            continue

        relative_path = prompt_path.relative_to(prompts_dir)
        searchable = relative_path.as_posix().lower()
        if filters and not all(part in searchable for part in filters):
            continue

        prompt = extract_prompt(prompt_path.read_text(encoding="utf-8"))
        if not prompt:
            raise ValueError(f"Prompt file is empty after extraction: {prompt_path}")

        jobs.append(
            PromptJob(
                prompt_path=prompt_path,
                relative_path=relative_path,
                output_dir=outputs_dir / relative_path.parent,
                slug=relative_path.stem,
                prompt=prompt,
            )
        )

    return jobs


def banner_prompt(prompt: str) -> str:
    return (
        f"{prompt}\n\n"
        "Generation instruction for this API request: generate only Output 1, the 3:1 ultrawide "
        "banner illustration. Do not include the thumbnail, do not create a split-screen image, "
        "and do not show multiple panels."
    )


def thumbnail_prompt(prompt: str, derived_from_banner: bool) -> str:
    prefix = (
        "Use the provided banner image as the visual source. "
        if derived_from_banner
        else ""
    )
    return (
        f"{prompt}\n\n"
        "Generation instruction for this API request: "
        f"{prefix}generate only Output 2, the 1600x896 thumbnail version. "
        "Preserve the same central objects, style, palette, composition logic, and visual identity. "
        "Do not include the banner, do not create a split-screen image, and do not show multiple panels."
    )


def image_bytes_from_response(response) -> bytes:
    if not response.data:
        raise RuntimeError("Image response did not contain any data items.")

    image = response.data[0]
    b64_json = getattr(image, "b64_json", None)
    if b64_json:
        return base64.b64decode(b64_json)

    url = getattr(image, "url", None)
    if url:
        with urllib.request.urlopen(url, timeout=120) as remote:
            return remote.read()

    raise RuntimeError("Image response contained neither b64_json nor url.")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def call_with_retries(
    description: str,
    attempts_after_first: int,
    retry_delay: float,
    request: Callable[[], bytes],
) -> bytes:
    attempts = attempts_after_first + 1
    for attempt in range(1, attempts + 1):
        try:
            return request()
        except (OpenAIError, OSError, RuntimeError) as exc:
            if attempt >= attempts:
                raise
            delay = retry_delay * (2 ** (attempt - 1))
            print(f"  {description} failed on attempt {attempt}/{attempts}: {exc}", file=sys.stderr)
            print(f"  retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)

    raise AssertionError("unreachable")


def generate_banner(client: OpenAI, args: argparse.Namespace, job: PromptJob) -> bytes:
    response = client.images.generate(
        model=args.model,
        prompt=banner_prompt(job.prompt),
        size=args.banner_size,
        quality=args.quality,
        output_format=args.output_format,
        n=1,
    )
    return image_bytes_from_response(response)


def generate_thumbnail_direct(client: OpenAI, args: argparse.Namespace, job: PromptJob) -> bytes:
    response = client.images.generate(
        model=args.model,
        prompt=thumbnail_prompt(job.prompt, derived_from_banner=False),
        size=args.thumbnail_size,
        quality=args.quality,
        output_format=args.output_format,
        n=1,
    )
    return image_bytes_from_response(response)


def generate_thumbnail_edit(client: OpenAI, args: argparse.Namespace, job: PromptJob, banner_path: Path) -> bytes:
    with banner_path.open("rb") as image_file:
        response = client.images.edit(
            model=args.model,
            image=image_file,
            prompt=thumbnail_prompt(job.prompt, derived_from_banner=True),
            size=args.thumbnail_size,
            quality=args.quality,
            output_format=args.output_format,
            n=1,
        )
    return image_bytes_from_response(response)


def run_job(client: OpenAI, args: argparse.Namespace, job: PromptJob, index: int, total: int) -> bool:
    banner_path = job.banner_path(args.output_format)
    thumbnail_path = job.thumbnail_path(args.output_format)
    print(f"[{index}/{total}] {job.relative_path}")

    if args.dry_run:
        print(f"  banner:    {banner_path}")
        print(f"  thumbnail: {thumbnail_path}")
        return True

    job.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if banner_path.exists() and not args.overwrite:
            print(f"  banner exists, skipping: {banner_path}")
        else:
            print(f"  generating banner -> {banner_path}")
            banner_data = call_with_retries(
                "banner generation",
                args.retries,
                args.retry_delay,
                lambda: generate_banner(client, args, job),
            )
            write_bytes(banner_path, banner_data)

        if thumbnail_path.exists() and not args.overwrite:
            print(f"  thumbnail exists, skipping: {thumbnail_path}")
        else:
            print(f"  generating thumbnail -> {thumbnail_path}")
            if args.thumbnail_mode == "edit":
                if not banner_path.exists():
                    raise RuntimeError(f"Cannot derive thumbnail because banner is missing: {banner_path}")
                thumbnail_data = call_with_retries(
                    "thumbnail edit",
                    args.retries,
                    args.retry_delay,
                    lambda: generate_thumbnail_edit(client, args, job, banner_path),
                )
            else:
                thumbnail_data = call_with_retries(
                    "thumbnail generation",
                    args.retries,
                    args.retry_delay,
                    lambda: generate_thumbnail_direct(client, args, job),
                )
            write_bytes(thumbnail_path, thumbnail_data)
    except Exception as exc:
        print(f"  failed: {exc}", file=sys.stderr)
        return False

    return True


def main() -> int:
    args = parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.prompts_dir.exists():
        print(f"Prompts directory does not exist: {args.prompts_dir}", file=sys.stderr)
        return 2

    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    jobs = discover_jobs(args.prompts_dir, args.outputs_dir, args.only)
    if args.limit is not None:
        jobs = jobs[: args.limit]

    if not jobs:
        print("No prompt files matched.")
        return 0

    print(f"Prompts: {len(jobs)}")
    print(f"Model: {args.model}")
    print(f"Quality: {args.quality}")
    print(f"Banner size: {args.banner_size}")
    print(f"Thumbnail size: {args.thumbnail_size}")
    print(f"Thumbnail mode: {args.thumbnail_mode}")
    print(f"Output format: {args.output_format}")
    if args.dry_run:
        print("Dry run: no API calls will be made.")

    client = OpenAI(timeout=args.timeout) if not args.dry_run else None
    failures: list[PromptJob] = []

    for index, job in enumerate(jobs, start=1):
        assert client is not None or args.dry_run
        ok = run_job(client, args, job, index, len(jobs))  # type: ignore[arg-type]
        if not ok:
            failures.append(job)
            if args.fail_fast:
                break
        if args.pause and index < len(jobs):
            time.sleep(args.pause)

    if failures:
        print("\nFailed prompt files:", file=sys.stderr)
        for job in failures:
            print(f"- {job.relative_path}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
