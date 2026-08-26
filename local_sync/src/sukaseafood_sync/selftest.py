from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import re
import ssl
from tempfile import TemporaryDirectory
from uuid import UUID

import certifi
import imagehash
from PIL import Image

from .downloader import DownloadResult, _validate_image
from .index import SyncIndex
from .manifest import ManifestRow
from .operations import apply_add


_HEX_16 = re.compile(r"[0-9a-f]{16}\Z", re.ASCII)


class SelfTestError(RuntimeError):
    """A stable, secret-free frozen diagnostic failure."""

    def __init__(self, code: str) -> None:
        if code not in {
            "IMAGE_ENCODE",
            "IMAGE_PHASH_MODULE_CDFLIB",
            "IMAGE_PHASH_MODULE_TEST_SUPPORT",
            "IMAGE_PHASH_MODULE_SCIPY",
            "IMAGE_PHASH_MODULE_NUMPY",
            "IMAGE_PHASH_MODULE_OTHER",
            "IMAGE_PHASH_BINARY",
            "IMAGE_PHASH_ATTRIBUTE",
            "IMAGE_PHASH_TYPE",
            "IMAGE_PHASH_VALUE",
            "IMAGE_PHASH_KEY",
            "IMAGE_PHASH_RUNTIME_ERROR",
            "IMAGE_PHASH_ASSERTION",
            "IMAGE_PHASH_INDEX",
            "IMAGE_PHASH_ARITHMETIC",
            "IMAGE_PHASH_NOT_IMPLEMENTED",
            "IMAGE_PHASH_RUNTIME",
            "IMAGE_PIPELINE",
            "FILESYSTEM",
            "CA",
        }:
            code = "UNEXPECTED"
        self.code = code
        super().__init__(code)


def _deterministic_png() -> bytes:
    image = Image.new("RGB", (32, 24))
    pixels = image.load()
    for y in range(24):
        for x in range(32):
            pixels[x, y] = (x * 7 % 256, y * 11 % 256, (x + y) * 13 % 256)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def run_self_test() -> None:
    """Exercise frozen ADD dependencies and local persistence without networking."""

    try:
        payload = _deterministic_png()
    except Exception:
        raise SelfTestError("IMAGE_ENCODE") from None

    try:
        with Image.open(BytesIO(payload)) as decoded:
            direct_hash = str(imagehash.phash(decoded.convert("RGB"))).lower()
        if _HEX_16.fullmatch(direct_hash) is None:
            raise ValueError
    except ModuleNotFoundError as error:
        if error.name == "scipy.special._cdflib":
            code = "IMAGE_PHASH_MODULE_CDFLIB"
        elif error.name == "unittest":
            code = "IMAGE_PHASH_MODULE_TEST_SUPPORT"
        elif error.name and error.name.startswith("scipy"):
            code = "IMAGE_PHASH_MODULE_SCIPY"
        elif error.name and error.name.startswith("numpy"):
            code = "IMAGE_PHASH_MODULE_NUMPY"
        else:
            code = "IMAGE_PHASH_MODULE_OTHER"
        raise SelfTestError(code) from None
    except (ImportError, OSError):
        raise SelfTestError("IMAGE_PHASH_BINARY") from None
    except AttributeError:
        raise SelfTestError("IMAGE_PHASH_ATTRIBUTE") from None
    except TypeError:
        raise SelfTestError("IMAGE_PHASH_TYPE") from None
    except ValueError:
        raise SelfTestError("IMAGE_PHASH_VALUE") from None
    except KeyError:
        raise SelfTestError("IMAGE_PHASH_KEY") from None
    except NotImplementedError:
        raise SelfTestError("IMAGE_PHASH_NOT_IMPLEMENTED") from None
    except RuntimeError:
        raise SelfTestError("IMAGE_PHASH_RUNTIME_ERROR") from None
    except AssertionError:
        raise SelfTestError("IMAGE_PHASH_ASSERTION") from None
    except IndexError:
        raise SelfTestError("IMAGE_PHASH_INDEX") from None
    except ArithmeticError:
        raise SelfTestError("IMAGE_PHASH_ARITHMETIC") from None
    except Exception:
        raise SelfTestError("IMAGE_PHASH_RUNTIME") from None

    try:
        decoded_format, perceptual_hash, width, height = _validate_image(
            BytesIO(payload)
        )
        if (
            decoded_format != "PNG"
            or _HEX_16.fullmatch(perceptual_hash) is None
            or (width, height) != (32, 24)
        ):
            raise ValueError
    except Exception:
        raise SelfTestError("IMAGE_PIPELINE") from None

    try:
        with TemporaryDirectory(prefix="sukaseafood-sync-self-test-") as raw_directory:
            root = Path(raw_directory) / "dataset"
            root.mkdir()
            candidate_id = UUID("22222222-2222-4222-8222-222222222222")
            review_id = UUID("33333333-3333-4333-8333-333333333333")
            batch_id = UUID("11111111-1111-4111-8111-111111111111")
            target_relative = PurePosixPath(f"images/SELF_TEST/{candidate_id}.image")
            staging = root.joinpath(*target_relative.parts).with_name(
                target_relative.name + ".part"
            )
            staging.parent.mkdir(parents=True)
            staging.write_bytes(payload)
            row = ManifestRow(
                batch_id=batch_id,
                action="ADD",
                candidate_id=candidate_id,
                review_id=review_id,
                review_version=1,
                species_code="SELF_TEST",
                target_relative_path=target_relative,
                previous_relative_path=None,
                preview_url="",
                original_url="",
                source_url="",
                creator=None,
                license="",
                license_url=None,
                attribution="",
            )
            downloaded = DownloadResult(
                staging_path=staging,
                sha256=sha256(payload).hexdigest(),
                phash=perceptual_hash,
                byte_count=len(payload),
                format=decoded_format,
                suffix=".png",
                width=width,
                height=height,
            )
            index = SyncIndex(root)
            result = apply_add(root, row, downloaded, index)
            expected_relative = target_relative.with_suffix(".png")
            stored = index.get_completed(candidate_id, review_id, 1, "ADD")
            if (
                result.relative_path != expected_relative
                or stored is None
                or stored.relative_path != expected_relative
                or index.get_add_intent(candidate_id, review_id, 1, "ADD") is not None
                or root.joinpath(*expected_relative.parts).read_bytes() != payload
                or staging.exists()
            ):
                raise ValueError
    except Exception:
        raise SelfTestError("FILESYSTEM") from None

    try:
        ca_path = Path(certifi.where())
        if not ca_path.is_file():
            raise ValueError
        context = ssl.create_default_context(cafile=str(ca_path))
        if not context.get_ca_certs():
            raise ValueError
    except Exception:
        raise SelfTestError("CA") from None
