"""Contact-anchor resampling for the tangent drawing tools.

The approach mirrors the vertex resampler's Kissing mode: tangency locations
are mandatory samples.  The circle and each selected source curve therefore
receive a real vertex at the same coordinate without requiring them to be
welded into shared topology.  Existing contacts in the selection are also
mandatory anchors, so adding another tangent does not destroy an earlier one.
"""

import math

import bmesh


_ANGLE_EPSILON = 1e-7
_LENGTH_EPSILON = 1e-10
_PARAM_EPSILON = 1e-5
_POINT_EPSILON = 1e-7
_TOUCH_EPSILON = 1e-4


def _allocate_segment_counts(lengths, total_segments):
    """Distribute segments proportionally while keeping one per interval."""
    interval_count = len(lengths)
    if interval_count == 0:
        return []

    total_segments = max(interval_count, int(total_segments))
    counts = [1] * interval_count
    remaining = total_segments - interval_count
    if remaining == 0:
        return counts

    total_length = sum(lengths)
    if total_length <= _LENGTH_EPSILON:
        for index in range(remaining):
            counts[index % interval_count] += 1
        return counts

    raw_extras = [
        remaining * (length / total_length)
        for length in lengths
    ]
    floor_extras = [int(math.floor(value)) for value in raw_extras]
    for index, extra in enumerate(floor_extras):
        counts[index] += extra

    leftovers = remaining - sum(floor_extras)
    ranked = sorted(
        range(interval_count),
        key=lambda index: (
            raw_extras[index] - floor_extras[index],
            lengths[index],
            -index,
        ),
        reverse=True,
    )
    for index in ranked[:leftovers]:
        counts[index] += 1
    return counts


def circle_points_with_tangent_anchors(
    center,
    radius,
    segments,
    axis_x,
    axis_y,
    tangent_points,
):
    """Sample a closed circle while retaining every tangency as a vertex."""
    segments = max(3, int(segments))
    anchors = []

    for point in tangent_points or []:
        radial = point - center
        angle = math.atan2(radial.dot(axis_y), radial.dot(axis_x))
        angle %= math.tau

        duplicate = False
        for existing_angle, existing_point in anchors:
            delta = abs(angle - existing_angle)
            delta = min(delta, math.tau - delta)
            if (
                delta <= _ANGLE_EPSILON
                and (point - existing_point).length <= _POINT_EPSILON
            ):
                duplicate = True
                break
        if not duplicate:
            anchors.append((angle, point.copy()))

    if not anchors:
        points = [
            center
            + axis_x * (math.cos(math.tau * index / segments) * radius)
            + axis_y * (math.sin(math.tau * index / segments) * radius)
            for index in range(segments)
        ]
        return points + [points[0].copy()]

    anchors.sort(key=lambda item: item[0])
    interval_angles = []
    for index, (angle, _point) in enumerate(anchors):
        next_angle = anchors[(index + 1) % len(anchors)][0]
        if index == len(anchors) - 1:
            next_angle += math.tau
        interval_angles.append(next_angle - angle)

    counts = _allocate_segment_counts(interval_angles, segments)
    points = []
    for index, (segment_count, anchor) in enumerate(zip(counts, anchors)):
        angle, contact_point = anchor
        span = interval_angles[index]
        for sample_index in range(segment_count):
            if sample_index == 0:
                point = contact_point.copy()
            else:
                sample_angle = angle + span * (sample_index / segment_count)
                point = (
                    center
                    + axis_x * (math.cos(sample_angle) * radius)
                    + axis_y * (math.sin(sample_angle) * radius)
                )
            points.append(point)

    return points + [points[0].copy()]


def _eval_spline_global(spline, parameter):
    segment_count = len(spline.segments)
    if segment_count == 0:
        return None

    if spline.is_closed:
        parameter %= segment_count
    else:
        parameter = max(0.0, min(float(segment_count), parameter))

    segment_index = int(math.floor(parameter))
    if segment_index >= segment_count:
        segment_index = segment_count - 1
        local_factor = 1.0
    else:
        local_factor = parameter - segment_index

    segment = spline.segments[segment_index]
    local_parameter = segment.t_start + local_factor * segment.dt
    return segment.eval(local_parameter)


def _project_spline_parameter(
    spline,
    point,
    samples_per_segment=10,
    refine_steps=18,
):
    """Find the stable closest global parameter to a contact point."""
    segment_count = len(spline.segments)
    if segment_count == 0:
        return 0.0

    best_parameter = 0.0
    best_distance_squared = float("inf")
    for segment_index in range(segment_count):
        for sample_index in range(samples_per_segment + 1):
            factor = sample_index / samples_per_segment
            parameter = segment_index + factor
            if spline.is_closed and parameter >= segment_count:
                parameter = 0.0
            position = _eval_spline_global(spline, parameter)
            distance_squared = (position - point).length_squared
            if distance_squared < best_distance_squared:
                best_distance_squared = distance_squared
                best_parameter = parameter

    half_window = 1.0 / samples_per_segment
    low = best_parameter - half_window
    high = best_parameter + half_window
    if not spline.is_closed:
        low = max(0.0, low)
        high = min(float(segment_count), high)

    for _index in range(refine_steps):
        third = (high - low) / 3.0
        first = low + third
        second = high - third
        first_distance = (
            _eval_spline_global(spline, first) - point
        ).length_squared
        second_distance = (
            _eval_spline_global(spline, second) - point
        ).length_squared
        if first_distance <= second_distance:
            high = second
        else:
            low = first

    parameter = (low + high) * 0.5
    if spline.is_closed:
        parameter %= segment_count
    else:
        parameter = max(0.0, min(float(segment_count), parameter))
    return parameter


def _parameter_distance(first, second, period=None):
    distance = abs(first - second)
    if period is not None and period > 0.0:
        distance = min(distance, period - distance)
    return distance


def _contact_anchors(spline, contacts):
    """Project and deduplicate mandatory contact points on one spline."""
    period = float(len(spline.segments)) if spline.is_closed else None
    anchors = []

    for contact in contacts or []:
        parameter = _project_spline_parameter(spline, contact)
        if period is not None:
            parameter %= period

        duplicate = False
        for existing_parameter, existing_contact in anchors:
            if (
                _parameter_distance(
                    parameter,
                    existing_parameter,
                    period,
                ) <= _PARAM_EPSILON
                or (contact - existing_contact).length <= _POINT_EPSILON
            ):
                duplicate = True
                break
        if not duplicate:
            anchors.append((parameter, contact.copy()))

    return sorted(anchors, key=lambda anchor: anchor[0])


def _interval_lut(spline, start, end):
    span = max(0.0, end - start)
    steps = max(24, int(math.ceil(span * 24.0)))
    parameters = []
    lengths = [0.0]
    previous = None

    for index in range(steps + 1):
        parameter = start + span * (index / steps)
        point = _eval_spline_global(spline, parameter)
        parameters.append(parameter)
        if previous is not None:
            lengths.append(lengths[-1] + (point - previous).length)
        previous = point
    return parameters, lengths


def _parameter_at_length(parameters, lengths, target_length):
    if target_length <= 0.0:
        return parameters[0]
    if target_length >= lengths[-1]:
        return parameters[-1]

    low = 0
    high = len(lengths) - 1
    while low + 1 < high:
        middle = (low + high) // 2
        if lengths[middle] < target_length:
            low = middle
        else:
            high = middle

    section_length = lengths[high] - lengths[low]
    if section_length <= _LENGTH_EPSILON:
        return parameters[low]
    factor = (target_length - lengths[low]) / section_length
    return (
        parameters[low]
        + (parameters[high] - parameters[low]) * factor
    )


def _sample_spline_interval(spline, start, end, segment_count):
    parameters, lengths = _interval_lut(spline, start, end)
    total_length = lengths[-1]
    coordinates = []

    for index in range(segment_count + 1):
        if index == 0:
            parameter = start
        elif index == segment_count:
            parameter = end
        elif total_length <= _LENGTH_EPSILON:
            parameter = start + (end - start) * (index / segment_count)
        else:
            parameter = _parameter_at_length(
                parameters,
                lengths,
                total_length * (index / segment_count),
            )
        coordinates.append(_eval_spline_global(spline, parameter))
    return coordinates, total_length


def _closed_curve_coordinates(spline, contacts, count):
    anchors = _contact_anchors(spline, contacts)
    if not anchors:
        return []

    segment_count = float(len(spline.segments))
    interval_data = []
    for index, anchor in enumerate(anchors):
        next_anchor = anchors[(index + 1) % len(anchors)]
        start = anchor[0]
        end = next_anchor[0]
        if index == len(anchors) - 1 or end <= start:
            end += segment_count
        _coordinates, length = _sample_spline_interval(
            spline,
            start,
            end,
            1,
        )
        interval_data.append((anchor, start, end, length))

    sample_counts = _allocate_segment_counts(
        [item[3] for item in interval_data],
        max(count, len(anchors)),
    )
    coordinates = []
    for sample_count, (anchor, start, end, _length) in zip(
        sample_counts,
        interval_data,
    ):
        section, _length = _sample_spline_interval(
            spline,
            start,
            end,
            sample_count,
        )
        section[0] = anchor[1].copy()
        coordinates.extend(section[:-1])
    return coordinates


def _open_curve_coordinates(spline, contacts, count):
    anchors = _contact_anchors(spline, contacts)
    segment_count = float(len(spline.segments))
    boundary_anchors = [
        (0.0, _eval_spline_global(spline, 0.0), False),
        *((parameter, contact, True) for parameter, contact in anchors),
        (
            segment_count,
            _eval_spline_global(spline, segment_count),
            False,
        ),
    ]

    unique_anchors = []
    for anchor in sorted(boundary_anchors, key=lambda item: item[0]):
        if (
            unique_anchors
            and abs(anchor[0] - unique_anchors[-1][0]) <= _PARAM_EPSILON
        ):
            # Prefer a supplied contact over the evaluated boundary point.
            if anchor[2]:
                unique_anchors[-1] = anchor
            continue
        unique_anchors.append(anchor)

    interval_data = []
    for anchor, next_anchor in zip(
        unique_anchors,
        unique_anchors[1:],
    ):
        _coordinates, length = _sample_spline_interval(
            spline,
            anchor[0],
            next_anchor[0],
            1,
        )
        interval_data.append((anchor, next_anchor, length))

    target_count = max(count, len(unique_anchors))
    sample_counts = _allocate_segment_counts(
        [item[2] for item in interval_data],
        target_count - 1,
    )
    coordinates = []
    for index, (
        sample_count,
        (anchor, next_anchor, _length),
    ) in enumerate(zip(sample_counts, interval_data)):
        section, _length = _sample_spline_interval(
            spline,
            anchor[0],
            next_anchor[0],
            sample_count,
        )
        section[0] = anchor[1].copy()
        section[-1] = next_anchor[1].copy()
        if index:
            section = section[1:]
        coordinates.extend(section)
    return coordinates


def _selected_contacts_for_sources(chains, source_indexes):
    """Find already coincident vertices between sources and the selection."""
    contacts = {index: [] for index in source_indexes}

    for source_index in source_indexes:
        source_points = chains[source_index][0]
        for other_index, (other_points, _closed, _vertices) in enumerate(
            chains
        ):
            if other_index == source_index:
                continue
            for source_point in source_points:
                for other_point in other_points:
                    if (
                        source_point - other_point
                    ).length <= _TOUCH_EPSILON:
                        # Snap the rebuilt source to the unchanged selected
                        # curve, matching Kissing's paired-contact behavior.
                        contacts[source_index].append(other_point.copy())
                        break

    return contacts


def _resize_curve_topology(bm, vertices, coordinates, closed):
    """Grow a selected chain when an extra mandatory anchor is required."""
    while len(vertices) < len(coordinates):
        edge_count = len(vertices) if closed else len(vertices) - 1
        longest_index = -1
        longest_length = -1.0

        for index in range(edge_count):
            next_index = (index + 1) % len(vertices)
            edge = bm.edges.get((vertices[index], vertices[next_index]))
            if edge is None:
                edge = bm.edges.get((vertices[next_index], vertices[index]))
            if edge is not None and edge.calc_length() > longest_length:
                longest_index = index
                longest_length = edge.calc_length()

        if longest_index < 0:
            return False

        next_index = (longest_index + 1) % len(vertices)
        first_vertex = vertices[longest_index]
        second_vertex = vertices[next_index]
        edge = bm.edges.get((first_vertex, second_vertex))
        if edge is None:
            edge = bm.edges.get((second_vertex, first_vertex))
        if edge is None:
            return False

        split_result = bmesh.utils.edge_split(edge, first_vertex, 0.5)
        new_vertex = (
            split_result[0]
            if isinstance(split_result[0], bmesh.types.BMVert)
            else split_result[1]
        )
        vertices.insert(longest_index + 1, new_vertex)

    if len(vertices) != len(coordinates):
        return False

    for vertex, coordinate in zip(vertices, coordinates):
        if not vertex.is_valid:
            return False
        vertex.co = coordinate
    return True


def resample_selected_curves_at_tangencies(
    obj,
    bm,
    tangent_points,
    source_chain_signatures=None,
):
    """Give each selected source curve a vertex at its tangent contact."""
    if not tangent_points:
        return False

    # Delayed import avoids a module cycle: circle_tools uses the circle sampler.
    from .operators.circle_tools import (
        CatmullRomSpline,
        get_selected_edge_chains,
    )

    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    selected_chains = get_selected_edge_chains(obj, include_verts=True)
    if len(selected_chains) < len(tangent_points):
        return False

    if source_chain_signatures:
        if len(source_chain_signatures) != len(tangent_points):
            return False

        chains_by_signature = {
            tuple(sorted({vertex.index for vertex in chain[2]})): (
                index,
                chain,
            )
            for index, chain in enumerate(selected_chains)
        }
        chains = []
        source_indexes = []
        for signature in source_chain_signatures:
            normalized = tuple(sorted(set(signature)))
            matched = chains_by_signature.get(normalized)
            if matched is None:
                return False
            index, chain = matched
            source_indexes.append(index)
            chains.append(chain)
    else:
        chains = selected_chains[:len(tangent_points)]
        source_indexes = list(range(len(chains)))

    world_matrix = obj.matrix_world
    inverse_world_matrix = world_matrix.inverted()
    prepared = []
    existing_contacts = _selected_contacts_for_sources(
        selected_chains,
        source_indexes,
    )

    for source_index, chain, contact in zip(
        source_indexes,
        chains,
        tangent_points,
    ):
        world_points, closed, vertices = chain
        spline = CatmullRomSpline(world_points, is_closed=closed)
        if not spline.segments:
            return False

        contacts = [
            *existing_contacts.get(source_index, []),
            contact,
        ]
        current_count = len(vertices)
        target_count = max(3 if closed else 2, current_count)

        if closed:
            world_coordinates = _closed_curve_coordinates(
                spline,
                contacts,
                target_count,
            )
        else:
            world_coordinates = _open_curve_coordinates(
                spline,
                contacts,
                target_count,
            )
        if not world_coordinates:
            return False

        local_coordinates = [
            inverse_world_matrix @ coordinate
            for coordinate in world_coordinates
        ]
        prepared.append((vertices, local_coordinates, closed))

    for vertices, coordinates, closed in prepared:
        if not _resize_curve_topology(
            bm,
            vertices,
            coordinates,
            closed,
        ):
            return False

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    return True
