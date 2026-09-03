"""Formatting for angular dimension labels."""

import math


def format_dimension_angle(measured_angle, _scene=None):
    """Format an angle dimension in degrees, without length-unit conversion."""
    degrees = math.degrees(abs(float(measured_angle)))
    if abs(degrees - round(degrees)) <= 1.0e-8:
        value = str(int(round(degrees)))
    else:
        value = f"{degrees:.2f}".rstrip("0").rstrip(".")
    return f"{value}\N{DEGREE SIGN}"
