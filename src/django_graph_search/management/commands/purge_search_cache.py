"""
Management command to purge expired file delta cache entries.

Usage:
    python manage.py purge_search_cache
    python manage.py purge_search_cache --dry-run

Only has effect when CACHE.BACKEND = "file".
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ...cache import FileDeltaCache, build_delta_cache
from ...settings import get_settings


class Command(BaseCommand):
    help = "Purge expired file-based delta cache entries (CACHE.BACKEND=file)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count expired files without deleting them.",
        )

    def handle(self, *args, **options) -> None:
        dry_run: bool = options["dry_run"]
        config = get_settings()
        cache = build_delta_cache(config)
        if not isinstance(cache, FileDeltaCache):
            self.stdout.write(
                self.style.WARNING(
                    f"CACHE.BACKEND is '{config.cache.backend}', not 'file' — nothing to do."
                )
            )
            return
        deleted = cache.purge_expired(dry_run=dry_run)
        action = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {deleted} expired cache files from {cache.directory}"
            )
        )
