# Contributing

Thanks for considering a contribution. This is a small tool, so the process is
informal.

## Before you start

For anything larger than a bug fix, please open an issue first. It is much
easier to agree on an approach than to rewrite a pull request.

## Development setup

You need the same things a user needs (see the README), plus a checkout:

    git clone https://github.com/3550625279-ux/paper-pipeline.git
    cd paper-pipeline

Run the self-check after touching configuration handling:

    python selftest.py

Reload the unpacked extension at chrome://extensions after changing anything
under extension/.

## Where things live

| Path | Responsibility |
| --- | --- |
| service.py | HTTP server, job queue, the five pipeline stages |
| metadata.py | DOI / arXiv / Crossref / OpenAlex resolution and page scraping |
| pdfsource.py | Downloading or reusing a local PDF |
| worker_translate.py | Runs inside the translator environment; drives pdf2zh-next and reports progress |
| zotero_api.py | Connector protocol client: create item, set collection, attach files |
| config.py | Configuration defaults and accessors |
| manage.py | The status / stop / logs commands behind pipeline.cmd |
| vendor/ | Bibliography detection and untranslated-prose scanning |
| extension/ | Chrome MV3 extension |

### Two things worth knowing

The translator runs as a **subprocess**, not an import. That is deliberate:
pdf2zh-next uses multiprocessing internally and lives in its own Python
environment, so code in worker_translate.py cannot import this project's
modules, and a crash there cannot take down the service.

Progress reporting depends on do_translate_async_stream, the translator's async
event stream. If you work in that area, confirm that overall_progress and stage
still arrive; some engine versions renamed those fields.

## Style

- Python: standard library only. Third-party dependencies belong to the
  translator environment, never to the service.
- Keep the UI plain. The extension deliberately avoids animation and decoration.
  It should feel like a tool, not a dashboard.
- Comments should explain why, not what.

## Reporting bugs

Please include:

- what you did, what you expected, what happened
- the last 30 lines of logs/service.log
- work/<job-id>/worker.err.log if the failure happened during translation
- your config.json **with the API key and any personal paths removed**

## Translations

The extension UI and the setup wizard are in Chinese today, because the primary
use case is reading English papers in Chinese. A language toggle would be a
welcome contribution; open an issue to discuss the approach first.

## License

By contributing you agree that your work is licensed under the MIT License.
