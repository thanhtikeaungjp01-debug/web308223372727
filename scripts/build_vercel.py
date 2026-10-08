"""Publish Flask assets to Vercel's CDN, preserving /static/... URLs."""
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "public" / "static"
    # This directory is generated and ignored by Git.
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(root / "static", destination)
    print("Published static assets to public/static for Vercel CDN.")


if __name__ == "__main__":
    main()
