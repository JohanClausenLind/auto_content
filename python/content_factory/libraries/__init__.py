"""The data-library registry: which downloaded corpora are on this host, and what reads them.

Not to be confused with :mod:`content_factory.datasets`, which is a different sense of the word —
that one compiles an uploaded CSV into a typed ``DatasetTable``. These are the *libraries*: the
reference corpus, the sound libraries, the staging assets, the LoRA training sets. "Library" is
the repo's own word for them already (``MediaLibrarySettings``, ``ReferenceSettings``, "the music
library"), which is why it is the one used here.

The weight store has had a declaration and a ``models/<category>/<Name>`` symlink index since
`models/weights.py`. Data had neither, and 39 GB was assembled into four unrelated places —

    /mnt/fast/reference             19 GB   seven human-interaction sources
    /mnt/fast/sound-libraries       12 GB   mixkit + 99Sounds + local renders
    /mnt/fast/datasets               6 GB   a HiDream LoRA training set
    /mnt/fast/models/blender-assets 614 MB  MakeHuman characters + retargeted CMU takes

— with nothing in the repo saying they existed. The consequence was not that they broke: three of
the four were never read by anything, and establishing that took grepping the facts of 236 runs.
A registry makes "is this reachable?" a question the code can answer.

:data:`LIBRARIES` is the declaration. :mod:`content_factory.libraries.index` is the only thing that
creates links from it, exactly as `weight_install` is for weights.
"""

from content_factory.libraries.registry import (
    LIBRARIES,
    DataLibrary,
    by_category,
    by_key,
    unreached,
)

__all__ = ["LIBRARIES", "DataLibrary", "by_category", "by_key", "unreached"]
