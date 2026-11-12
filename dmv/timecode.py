from fractions import Fraction


def fcp_time(value):
    """FCPXML rational time ('1001/30000s', '5s') -> Fraction seconds."""
    if not value:
        return Fraction(0)
    value = value.strip()
    if value.endswith("s"):
        value = value[:-1]
    return Fraction(value) if value else Fraction(0)


def tc(seconds, fps=25):
    """Seconds -> HH:MM:SS:FF."""
    seconds = max(0.0, float(seconds))
    total_frames = int(round(seconds * fps))
    ff = total_frames % int(round(fps))
    s = total_frames // int(round(fps))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


def mmss(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
