"""Characters3p B19/B58/B85 size ratios, with B9 result rounding kept at boundaries."""

from fractions import Fraction

from wayfarer.errors import ValidationError

# A six-step change is exactly a factor of ten. Keep the printed intermediate
# dimensions, rather than estimating them with a floating-point cube root.
_DIMENSIONS = (Fraction(2), Fraction(3), Fraction(5), Fraction(7), Fraction(10), Fraction(15))


def dimension_yards(size_modifier: int) -> Fraction:
    """Extend B19's repeating table without discarding fractional dimensions."""
    if type(size_modifier) is not int:
        raise ValidationError("Size Modifier must be an integer")
    decades, step = divmod(size_modifier, 6)
    multiplier = Fraction(10**decades) if decades >= 0 else Fraction(1, 10**-decades)
    return _DIMENSIONS[step] * multiplier


def height_ratio(native_sm: int, delta: int) -> Fraction:
    return dimension_yards(native_sm + delta) / dimension_yards(native_sm)


def shrinking_weight_ratio(levels: int) -> Fraction:
    """B85: divide by ten per full two levels, then by three for an odd level."""
    if type(levels) is not int or levels < 0:
        raise ValidationError("Shrinking levels must be a nonnegative integer")
    pairs, odd = divmod(levels, 2)
    return Fraction(1, 10**pairs * (3 if odd else 1))


def reduced_result(value: int, ratio: Fraction) -> int:
    """B9 rounds character feats and combat results down, after exact scaling."""
    if type(value) is not int or value < 0 or not 0 < ratio <= 1:
        raise ValidationError("Invalid reduced character feat or combat result")
    scaled = value * ratio
    return scaled.numerator // scaled.denominator


def growth_minimum_st(native_sm: int, levels: int) -> int:
    """B58 requires purchased ST of at least five times final height in yards."""
    if type(levels) is not int or levels < 0:
        raise ValidationError("Growth levels must be a nonnegative integer")
    minimum = 5 * dimension_yards(native_sm + levels)
    return -(-minimum.numerator // minimum.denominator)
