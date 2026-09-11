"""Deterministic image operations the control plane can do itself: no GPU, no skill, no subprocess.

Everything here is pure — same bytes in, same bytes out, on any machine — so it can sit inside a
stage's ``input_hash`` and be tested in the core suite.
"""

from content_factory.imaging.film import FilmResponse, apply_film_response

__all__ = ["FilmResponse", "apply_film_response"]
