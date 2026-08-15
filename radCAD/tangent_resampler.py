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


# -------------------------------------------------------------------------
# Tangify support for completed tangent-line tools
# -------------------------------------------------------------------------

_TANGIFY_COARSE_STEPS = 16
_TANGIFY_REFINE_STEPS = 48


def _ordered_closed_chain_from_signature(bm, signature):
    """Recover one ordered closed loop from the indexes captured by a tool."""
    indexes = tuple(sorted(set(signature or ())))
    if len(indexes) < 3:
        return None

    bm.verts.ensure_lookup_table()
    if indexes[-1] >= len(bm.verts):
        return None

    vertices = [bm.verts[index] for index in indexes]
    if any(not vertex.is_valid for vertex in vertices):
        return None

    vertex_set = set(vertices)
    adjacency = {}
    for vertex in vertices:
        neighbors = {
            edge.other_vert(vertex)
            for edge in vertex.link_edges
            if edge.other_vert(vertex) in vertex_set
        }
        if len(neighbors) != 2:
            # Tangify deliberately supports closed curves only.
            return None
        adjacency[vertex] = sorted(neighbors, key=lambda item: item.index)

    start = min(vertices, key=lambda item: item.index)
    ordered = []
    previous = None
    current = start
    while len(ordered) < len(vertices):
        if current in ordered:
            return None
        ordered.append(current)

        candidates = adjacency[current]
        if previous is None:
            next_vertex = candidates[0]
        else:
            next_vertex = next(
                (candidate for candidate in candidates if candidate is not previous),
                None,
            )
            if next_vertex is None:
                return None
        previous, current = current, next_vertex

    if current is not start:
        return None
    return ordered


def _tangify_tangent_global(spline, parameter, step=1e-4):
    segment_count = float(len(spline.segments))
    if segment_count <= 0.0:
        return None

    if spline.is_closed:
        before = _eval_spline_global(spline, (parameter - step) % segment_count)
        after = _eval_spline_global(spline, (parameter + step) % segment_count)
    else:
        before = _eval_spline_global(spline, max(0.0, parameter - step))
        after = _eval_spline_global(
            spline,
            min(segment_count, parameter + step),
        )
    tangent = after - before
    if tangent.length_squared <= _LENGTH_EPSILON * _LENGTH_EPSILON:
        return None
    return tangent.normalized()


def _tangify_parameter_distance(spline, first, second):
    distance = abs(first - second)
    if spline.is_closed:
        period = float(len(spline.segments))
        distance %= period
        return min(distance, period - distance)
    return distance


def _tangify_clamp_parameter(spline, value, seed, window):
    if spline.is_closed:
        period = float(len(spline.segments))
        delta = (value - seed + period * 0.5) % period - period * 0.5
        value = seed + delta
    value = max(seed - window, min(seed + window, value))
    if spline.is_closed:
        return value % len(spline.segments)
    return max(0.0, min(float(len(spline.segments)), value))


def _tangify_alignment_error(spline_a, spline_b, t_a, t_b):
    point_a = _eval_spline_global(spline_a, t_a)
    point_b = _eval_spline_global(spline_b, t_b)
    chord = point_b - point_a
    if chord.length_squared <= _LENGTH_EPSILON * _LENGTH_EPSILON:
        return float("inf"), point_a, point_b

    tangent_a = _tangify_tangent_global(spline_a, t_a)
    tangent_b = _tangify_tangent_global(spline_b, t_b)
    if tangent_a is None or tangent_b is None:
        return float("inf"), point_a, point_b

    direction = chord.normalized()
    dot_a = max(-1.0, min(1.0, tangent_a.dot(direction)))
    dot_b = max(-1.0, min(1.0, tangent_b.dot(direction)))
    error = (1.0 - dot_a * dot_a) + (1.0 - dot_b * dot_b)
    return error, point_a, point_b


def _tangify_guided_score(
    spline_a,
    spline_b,
    t_a,
    t_b,
    seed_a,
    seed_b,
    window_a,
    window_b,
    guide_direction,
):
    error, point_a, point_b = _tangify_alignment_error(
        spline_a,
        spline_b,
        t_a,
        t_b,
    )
    if not math.isfinite(error):
        return error

    chord = (point_b - point_a).normalized()
    direction_dot = max(-1.0, min(1.0, chord.dot(guide_direction)))
    direction_error = 1.0 - direction_dot
    drift_a = _tangify_parameter_distance(spline_a, t_a, seed_a) / window_a
    drift_b = _tangify_parameter_distance(spline_b, t_b, seed_b) / window_b
    return (
        error
        + 0.02 * direction_error
        + 0.002 * (drift_a * drift_a + drift_b * drift_b)
    )


def _tangify_common_tangent(
    spline_a,
    spline_b,
    seed_a,
    seed_b,
    guide_direction,
):
    """Validate/refine a two-curve line using rCAD Tangify's guide solve."""
    count_a = float(len(spline_a.segments))
    count_b = float(len(spline_b.segments))
    window_a = min(count_a * 0.25, max(1.5, count_a * 0.10))
    window_b = min(count_b * 0.25, max(1.5, count_b * 0.10))

    best_t_a = seed_a
    best_t_b = seed_b
    best_score = float("inf")
    for index_a in range(_TANGIFY_COARSE_STEPS + 1):
        raw_a = (
            seed_a
            - window_a
            + 2.0 * window_a * index_a / _TANGIFY_COARSE_STEPS
        )
        t_a = _tangify_clamp_parameter(
            spline_a,
            raw_a,
            seed_a,
            window_a,
        )
        for index_b in range(_TANGIFY_COARSE_STEPS + 1):
            raw_b = (
                seed_b
                - window_b
                + 2.0 * window_b * index_b / _TANGIFY_COARSE_STEPS
            )
            t_b = _tangify_clamp_parameter(
                spline_b,
                raw_b,
                seed_b,
                window_b,
            )
            score = _tangify_guided_score(
                spline_a,
                spline_b,
                t_a,
                t_b,
                seed_a,
                seed_b,
                window_a,
                window_b,
                guide_direction,
            )
            if score < best_score:
                best_score = score
                best_t_a = t_a
                best_t_b = t_b

    step_a = 2.0 * window_a / _TANGIFY_COARSE_STEPS
    step_b = 2.0 * window_b / _TANGIFY_COARSE_STEPS
    for _iteration in range(_TANGIFY_REFINE_STEPS):
        candidate_t_a = best_t_a
        candidate_t_b = best_t_b
        candidate_score = best_score
        for offset_a in (-step_a, 0.0, step_a):
            t_a = _tangify_clamp_parameter(
                spline_a,
                best_t_a + offset_a,
                seed_a,
                window_a,
            )
            for offset_b in (-step_b, 0.0, step_b):
                t_b = _tangify_clamp_parameter(
                    spline_b,
                    best_t_b + offset_b,
                    seed_b,
                    window_b,
                )
                score = _tangify_guided_score(
                    spline_a,
                    spline_b,
                    t_a,
                    t_b,
                    seed_a,
                    seed_b,
                    window_a,
                    window_b,
                    guide_direction,
                )
                if score < candidate_score:
                    candidate_score = score
                    candidate_t_a = t_a
                    candidate_t_b = t_b

        moved = (
            _tangify_parameter_distance(
                spline_a,
                candidate_t_a,
                best_t_a,
            )
            > 1.0e-12
            or _tangify_parameter_distance(
                spline_b,
                candidate_t_b,
                best_t_b,
            )
            > 1.0e-12
        )
        best_t_a = candidate_t_a
        best_t_b = candidate_t_b
        best_score = candidate_score
        if not moved:
            step_a *= 0.5
            step_b *= 0.5
            if max(step_a, step_b) <= 1.0e-8:
                break

    # Tangify removes branch-selection penalties for final convergence.
    pure_score, _point_a, _point_b = _tangify_alignment_error(
        spline_a,
        spline_b,
        best_t_a,
        best_t_b,
    )
    for _iteration in range(_TANGIFY_REFINE_STEPS):
        candidate_t_a = best_t_a
        candidate_t_b = best_t_b
        candidate_score = pure_score
        for offset_a in (-step_a, 0.0, step_a):
            t_a = _tangify_clamp_parameter(
                spline_a,
                best_t_a + offset_a,
                seed_a,
                window_a,
            )
            for offset_b in (-step_b, 0.0, step_b):
                t_b = _tangify_clamp_parameter(
                    spline_b,
                    best_t_b + offset_b,
                    seed_b,
                    window_b,
                )
                score, _point_a, _point_b = _tangify_alignment_error(
                    spline_a,
                    spline_b,
                    t_a,
                    t_b,
                )
                if score < candidate_score:
                    candidate_score = score
                    candidate_t_a = t_a
                    candidate_t_b = t_b

        moved = (
            _tangify_parameter_distance(
                spline_a,
                candidate_t_a,
                best_t_a,
            )
            > 1.0e-12
            or _tangify_parameter_distance(
                spline_b,
                candidate_t_b,
                best_t_b,
            )
            > 1.0e-12
        )
        best_t_a = candidate_t_a
        best_t_b = candidate_t_b
        pure_score = candidate_score
        if not moved:
            step_a *= 0.5
            step_b *= 0.5
            if max(step_a, step_b) <= 1.0e-10:
                break

    error, _point_a, _point_b = _tangify_alignment_error(
        spline_a,
        spline_b,
        best_t_a,
        best_t_b,
    )
    return math.isfinite(error)


def _tangify_single_curve_solution(spline, seed, direction):
    """Run Tangify's local single-curve tangent/normal branch solve."""
    seed_tangent = _tangify_tangent_global(spline, seed)
    if seed_tangent is None:
        return False

    seed_dot = abs(seed_tangent.dot(direction))
    tangent_relation = seed_dot >= 0.70710678
    count = float(len(spline.segments))
    window = min(count * 0.20, max(1.0, count * 0.08))

    def score(parameter):
        tangent = _tangify_tangent_global(spline, parameter)
        if tangent is None:
            return float("inf")
        dot = max(-1.0, min(1.0, abs(tangent.dot(direction))))
        alignment = 1.0 - dot * dot if tangent_relation else dot * dot
        drift = _tangify_parameter_distance(spline, parameter, seed) / window
        return alignment + 0.001 * drift * drift

    best_t = seed
    best_score = float("inf")
    for index in range(_TANGIFY_COARSE_STEPS + 1):
        raw = (
            seed
            - window
            + 2.0 * window * index / _TANGIFY_COARSE_STEPS
        )
        parameter = _tangify_clamp_parameter(spline, raw, seed, window)
        candidate_score = score(parameter)
        if candidate_score < best_score:
            best_t = parameter
            best_score = candidate_score

    step = 2.0 * window / _TANGIFY_COARSE_STEPS
    for _iteration in range(_TANGIFY_REFINE_STEPS):
        candidate_t = best_t
        candidate_score = best_score
        for offset in (-step, 0.0, step):
            parameter = _tangify_clamp_parameter(
                spline,
                best_t + offset,
                seed,
                window,
            )
            candidate = score(parameter)
            if candidate < candidate_score:
                candidate_t = parameter
                candidate_score = candidate
        moved = (
            _tangify_parameter_distance(spline, candidate_t, best_t)
            > 1.0e-12
        )
        best_t = candidate_t
        best_score = candidate_score
        if not moved:
            step *= 0.5
            if step <= 1.0e-9:
                break
    return math.isfinite(best_score)


def _tangify_spline_scale(spline):
    samples = [
        _eval_spline_global(spline, index)
        for index in range(len(spline.segments))
    ]
    if not samples:
        return 1.0
    minimum = samples[0].copy()
    maximum = samples[0].copy()
    for point in samples[1:]:
        minimum.x = min(minimum.x, point.x)
        minimum.y = min(minimum.y, point.y)
        minimum.z = min(minimum.z, point.z)
        maximum.x = max(maximum.x, point.x)
        maximum.y = max(maximum.y, point.y)
        maximum.z = max(maximum.z, point.z)
    return max((maximum - minimum).length, 1.0e-6)


def _tangify_project(spline, point):
    parameter = _project_spline_parameter(spline, point)
    projected = _eval_spline_global(spline, parameter)
    return parameter, (projected - point).length


def _match_completed_guide(curves, guide_vertices):
    matches = []
    for guide_vertex in (guide_vertices[0], guide_vertices[-1]):
        candidates = []
        for curve_index, curve in enumerate(curves):
            parameter, distance = _tangify_project(
                curve["spline"],
                guide_vertex.co,
            )
            candidates.append((distance, curve_index, parameter))
        if not candidates:
            return None
        matches.append(min(candidates, key=lambda item: item[0]))

    start_match, end_match = matches
    if start_match[1] == end_match[1]:
        return None

    for match in matches:
        curve = curves[match[1]]
        tolerance = max(
            1.0e-4,
            _tangify_spline_scale(curve["spline"]) * 0.12,
        )
        if match[0] > tolerance:
            return None

    return {
        "index_a": start_match[1],
        "index_b": end_match[1],
        "t_a": start_match[2],
        "t_b": end_match[2],
    }


def _match_completed_single_guide(curve, guide_vertices):
    best = None
    for endpoint_index in (0, -1):
        parameter, distance = _tangify_project(
            curve["spline"],
            guide_vertices[endpoint_index].co,
        )
        candidate = {
            "endpoint_index": endpoint_index,
            "t": parameter,
            "distance": distance,
        }
        if best is None or distance < best["distance"]:
            best = candidate

    tolerance = max(
        1.0e-4,
        _tangify_spline_scale(curve["spline"]) * 0.12,
    )
    return best if best is not None and best["distance"] <= tolerance else None


def tangify_created_line(obj, bm, source_chain_signatures, guide_vertices):
    """Tangify source loops using a line that has just been committed.

    This is a self-contained guide-driven Tangify implementation adapted to
    the drawing tools' recorded source-loop signatures.  It preserves each
    curve's vertex count, pins a curve vertex to every guide endpoint, and
    leaves the newly created line fixed.
    """
    if len(guide_vertices) < 2 or len(source_chain_signatures) not in {1, 2}:
        return False
    if any(not vertex.is_valid for vertex in guide_vertices):
        return False

    # Delayed import avoids the existing circle-tools/resampler module cycle.
    from .operators.circle_tools import CatmullRomSpline

    curves = []
    seen_signatures = set()
    for signature in source_chain_signatures:
        normalized = tuple(sorted(set(signature or ())))
        if normalized in seen_signatures:
            return False
        seen_signatures.add(normalized)

        vertices = _ordered_closed_chain_from_signature(bm, normalized)
        if vertices is None:
            return False
        spline = CatmullRomSpline(
            [vertex.co.copy() for vertex in vertices],
            is_closed=True,
        )
        if not spline.segments:
            return False
        curves.append({
            "vertices": vertices,
            "spline": spline,
            "contacts": [],
        })

    start = guide_vertices[0].co
    end = guide_vertices[-1].co
    direction = end - start
    if direction.length_squared <= _LENGTH_EPSILON * _LENGTH_EPSILON:
        return False
    direction.normalize()

    if len(curves) == 2:
        match = _match_completed_guide(curves, guide_vertices)
        if match is None:
            return False
        curve_a = curves[match["index_a"]]
        curve_b = curves[match["index_b"]]
        if not _tangify_common_tangent(
            curve_a["spline"],
            curve_b["spline"],
            match["t_a"],
            match["t_b"],
            direction,
        ):
            return False
        curve_a["contacts"].append(start.copy())
        curve_b["contacts"].append(end.copy())
    else:
        curve = curves[0]
        match = _match_completed_single_guide(curve, guide_vertices)
        if match is None:
            return False
        if not _tangify_single_curve_solution(
            curve["spline"],
            match["t"],
            direction,
        ):
            return False
        contact_vertex = guide_vertices[match["endpoint_index"]]
        curve["contacts"].append(contact_vertex.co.copy())

    prepared = []
    for curve in curves:
        coordinates = _closed_curve_coordinates(
            curve["spline"],
            curve["contacts"],
            len(curve["vertices"]),
        )
        if len(coordinates) != len(curve["vertices"]):
            return False
        prepared.append((curve["vertices"], coordinates))

    # Match Tangify's transactional behavior: modify only after every source
    # loop has produced a valid target coordinate set.
    for vertices, coordinates in prepared:
        for vertex, coordinate in zip(vertices, coordinates):
            if not vertex.is_valid:
                return False
            vertex.co = coordinate
            vertex.select = True
    return True
