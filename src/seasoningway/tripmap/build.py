"""把一趟旅程的地圖來源和快取轉成 HTML。"""
from pathlib import Path

from ..common import Problem
from . import render
from .data import MapTrip, page_data


def load_page(trip_dir: Path) -> render.MapPage:
    try:
        trip = MapTrip(trip_dir)
        return render.load_page(trip, page_data(trip))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
        raise Problem(f"trips/{trip_dir.name}/map：地圖來源或快取格式錯誤（{error}）") from None
