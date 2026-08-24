"""
Paste this whole file into Maya's Python Script Editor and run it.

First run: select one polygon-mesh target, then run.
Later runs: edit the two NURBS controls and/or the attributes on
`sandHeap_size_CTRL`, then run the same script again to rebuild.
"""

import math
import random
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    from PySide6 import QtCore
except ImportError:
    try:
        from PySide2 import QtCore
    except ImportError:
        QtCore = None


# -----------------------------------------------------------------------------
# Names. Change the prefix if you want more than one independently editable heap.
# -----------------------------------------------------------------------------
PREFIX = "sandHeap"
SIZE_CTRL = PREFIX + "_size_CTRL"
PROFILE_CTRL = PREFIX + "_falloff_CTRL"
OUTPUT_GEO = PREFIX + "_GEO"
MATERIAL = PREFIX + "_MAT"
SHADING_GROUP = MATERIAL + "SG"
CONTROL_WINDOW = PREFIX + "_controls_WINDOW"
SEED_SLIDER = PREFIX + "_seed_SLIDER"


class _ProgressWindow(object):
    """Interruptible Maya progress window with reliable repaint behavior."""

    def __init__(self, enabled=True):
        self.enabled = bool(enabled)
        self.is_open = False
        self.cancelled = False
        self._last_cancel_poll = 0.0

    @staticmethod
    def _pump_events():
        if QtCore is not None:
            QtCore.QCoreApplication.processEvents()
        # Native Maya controls also need the command loop to yield.
        cmds.pause(seconds=0.001)

    def begin_phase(self, maximum, status):
        # Maya does not reliably accept a changed maximum on an existing
        # progressWindow. Reopen it for every phase, as Interselect does.
        self.close()
        if not self.enabled or self.cancelled:
            return
        try:
            creation_result = cmds.progressWindow(
                title="Build Sand Heap",
                status=status,
                progress=0,
                maxValue=max(int(maximum), 1),
                isInterruptable=True,
            )
            # Some Maya versions return None on success. Only explicit False
            # means the window was not acquired.
            self.is_open = creation_result is not False
            if self.is_open:
                self._pump_events()
        except RuntimeError:
            # Building can continue if another progress window owns the UI.
            self.is_open = False

    def update(self, progress, status):
        if not self.is_open:
            return
        try:
            cmds.progressWindow(
                edit=True,
                progress=max(int(progress), 0),
                status=status,
            )
            self._pump_events()
        except RuntimeError:
            # A transient repaint failure should not abort geometry creation.
            pass

    def cancel_requested(self, force=False):
        if self.cancelled:
            return True
        if not self.is_open:
            return False
        now = time.perf_counter()
        if not force and now - self._last_cancel_poll < 0.05:
            return False
        self._last_cancel_poll = now
        try:
            self._pump_events()
            self.cancelled = bool(
                cmds.progressWindow(query=True, isCancelled=True)
            )
        except RuntimeError:
            self.is_open = False
        return self.cancelled

    def close(self):
        if not self.is_open:
            return
        try:
            cmds.progressWindow(endProgress=True)
        except RuntimeError:
            pass
        self.is_open = False
        self._pump_events()


def _mesh_from_selection():
    """Return (transform, shape) for the first selected polygon mesh."""
    for node in cmds.ls(selection=True, objectsOnly=True, long=True) or []:
        if cmds.nodeType(node) == "mesh":
            shapes = [node]
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            transform = parents[0] if parents else None
        else:
            shapes = cmds.listRelatives(
                node, shapes=True, noIntermediate=True, fullPath=True, type="mesh"
            ) or []
            transform = node
        if transform and transform.rsplit("|", 1)[-1] == OUTPUT_GEO:
            continue
        if transform and shapes:
            return transform, shapes[0]
    return None, None


def _remembered_mesh():
    if not cmds.objExists(SIZE_CTRL + ".targetGeometry"):
        return None, None
    sources = cmds.listConnections(
        SIZE_CTRL + ".targetGeometry", source=True, destination=False
    ) or []
    for transform in sources:
        shapes = cmds.listRelatives(
            transform, shapes=True, noIntermediate=True, fullPath=True, type="mesh"
        ) or []
        if shapes:
            return transform, shapes[0]
    return None, None


def _add_attr(node, name, attr_type, default, minimum=None, maximum=None):
    plug = node + "." + name
    if cmds.objExists(plug):
        return
    kwargs = {
        "longName": name,
        "attributeType": attr_type,
        "defaultValue": default,
        "keyable": True,
    }
    if minimum is not None:
        kwargs["minValue"] = minimum
    if maximum is not None:
        kwargs["maxValue"] = maximum
    cmds.addAttr(node, **kwargs)


def _ensure_controls(target_transform):
    created_size = False
    if not cmds.objExists(SIZE_CTRL):
        bbox = cmds.exactWorldBoundingBox(target_transform)
        cx = (bbox[0] + bbox[3]) * 0.5
        cz = (bbox[2] + bbox[5]) * 0.5
        width = max(bbox[3] - bbox[0], bbox[5] - bbox[2])
        radius = max(width * 0.2, 1.0)
        ctrl = cmds.circle(
            name=SIZE_CTRL,
            normal=(0, 1, 0),
            radius=radius,
            sections=24,
            constructionHistory=False,
        )[0]
        cmds.xform(ctrl, worldSpace=True, translation=(cx, bbox[4] + radius * 0.1, cz))
        created_size = True

    if not cmds.objExists(SIZE_CTRL + ".targetGeometry"):
        cmds.addAttr(SIZE_CTRL, longName="targetGeometry", attributeType="message")

    _add_attr(SIZE_CTRL, "grainCount", "long", 3500, 100, 100000)
    _add_attr(SIZE_CTRL, "heapHeight", "double", 3.0, 0.01, 100000.0)
    _add_attr(SIZE_CTRL, "grainSize", "double", 0.16, 0.001, 10000.0)
    _add_attr(SIZE_CTRL, "grainSizeVariation", "double", 0.30, 0.0, 0.95)
    _add_attr(SIZE_CTRL, "grainFlattening", "double", 0.68, 0.1, 2.0)
    _add_attr(SIZE_CTRL, "rotationVariance", "double", 12.0, 0.0, 90.0)
    _add_attr(SIZE_CTRL, "falloffPower", "double", 1.0, 0.05, 20.0)
    _add_attr(SIZE_CTRL, "seed", "long", 12345, 0, 2147483647)
    _add_attr(SIZE_CTRL, "autoIncrementSeed", "bool", 1)
    _add_attr(SIZE_CTRL, "maxFailedPlacements", "long", 3000, 100, 1000000)
    _add_attr(SIZE_CTRL, "useProjectionCache", "bool", 1)
    quality_upgrade = not cmds.objExists(SIZE_CTRL + ".packingTightness")
    _add_attr(SIZE_CTRL, "packingTightness", "double", 0.94, 0.80, 1.05)
    _add_attr(SIZE_CTRL, "highDetailGrains", "bool", 1)
    _add_attr(SIZE_CTRL, "softEdges", "bool", 1)
    _add_attr(SIZE_CTRL, "showProgress", "bool", 1)
    if quality_upgrade:
        # Migrate heaps made with the first performance release to the more
        # natural default while retaining the explicit fast-mode toggles.
        cmds.setAttr(SIZE_CTRL + ".highDetailGrains", True)
        cmds.setAttr(SIZE_CTRL + ".softEdges", True)

    # A normalized side-view graph, drawn at a useful size in local XY.
    # X = distance from center; Y = relative pile height.
    if not cmds.objExists(PROFILE_CTRL):
        profile = cmds.curve(
            name=PROFILE_CTRL,
            degree=3,
            point=[
                (0.0, 3.0, 0.0),
                (0.45, 3.0, 0.0),
                (1.25, 2.8, 0.0),
                (2.35, 2.15, 0.0),
                (3.45, 1.25, 0.0),
                (4.35, 0.42, 0.0),
                (5.0, 0.0, 0.0),
            ],
        )
        size_bbox = cmds.exactWorldBoundingBox(SIZE_CTRL)
        cmds.xform(
            profile,
            worldSpace=True,
            translation=(size_bbox[3] + 1.0, size_bbox[4] + 0.5, size_bbox[5]),
        )

    if created_size:
        # Set a more proportional initial height and grain size.
        bbox = cmds.exactWorldBoundingBox(SIZE_CTRL)
        diameter = max(bbox[3] - bbox[0], bbox[5] - bbox[2])
        cmds.setAttr(SIZE_CTRL + ".heapHeight", max(diameter * 0.28, 0.5))
        cmds.setAttr(SIZE_CTRL + ".grainSize", max(diameter * 0.014, 0.025))


def _connect_target(target_transform):
    dst = SIZE_CTRL + ".targetGeometry"
    old = cmds.listConnections(dst, source=True, destination=False, plugs=True) or []
    for src in old:
        try:
            cmds.disconnectAttr(src, dst)
        except RuntimeError:
            pass
    cmds.connectAttr(target_transform + ".message", dst, force=True)


def _dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _curve_points(curve_transform, samples):
    shapes = cmds.listRelatives(
        curve_transform, shapes=True, noIntermediate=True, fullPath=True, type="nurbsCurve"
    ) or []
    if not shapes:
        raise RuntimeError("{} has no NURBS curve shape.".format(curve_transform))
    fn = om.MFnNurbsCurve(_dag_path(shapes[0]))
    start, end = fn.knotDomain
    result = []
    for i in range(samples):
        t = start + (end - start) * (float(i) / float(samples - 1))
        result.append(fn.getPointAtParam(t, om.MSpace.kObject))
    return result


def _point_in_polygon(x, z, polygon):
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, zi = polygon[i]
        xj, zj = polygon[j]
        if ((zi > z) != (zj > z)) and (
            x < (xj - xi) * (z - zi) / ((zj - zi) or 1.0e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def _normalized_radius(x, z, polygon):
    """Distance from origin to point divided by boundary distance on the same ray."""
    length = math.sqrt(x * x + z * z)
    if length < 1.0e-10:
        return 0.0
    dx, dz = x / length, z / length
    nearest = None
    for i in range(len(polygon)):
        px, pz = polygon[i]
        qx, qz = polygon[(i + 1) % len(polygon)]
        sx, sz = qx - px, qz - pz
        denom = dx * sz - dz * sx
        if abs(denom) < 1.0e-12:
            continue
        ray_t = (px * sz - pz * sx) / denom
        seg_t = (px * dz - pz * dx) / denom
        if ray_t > 0.0 and -1.0e-6 <= seg_t <= 1.000001:
            if nearest is None or ray_t < nearest:
                nearest = ray_t
    if not nearest:
        return 1.0
    return max(0.0, min(1.0, length / nearest))


def _profile_samples():
    points = _curve_points(PROFILE_CTRL, 300)
    pairs = sorted((p.x, p.y) for p in points)
    min_x, max_x = pairs[0][0], pairs[-1][0]
    max_y = max(p[1] for p in pairs)
    if max_x - min_x < 1.0e-8 or max_y <= 1.0e-8:
        raise RuntimeError(
            "The falloff curve needs a left-to-right X range and positive Y height."
        )
    return [((x - min_x) / (max_x - min_x), max(0.0, y / max_y)) for x, y in pairs]


def _profile_value(radius, samples):
    if radius <= samples[0][0]:
        return samples[0][1]
    for i in range(1, len(samples)):
        x0, y0 = samples[i - 1]
        x1, y1 = samples[i]
        if x1 >= radius:
            if x1 - x0 < 1.0e-10:
                return y1
            blend = (radius - x0) / (x1 - x0)
            return y0 + (y1 - y0) * blend
    return samples[-1][1]


def _build_footprint_lookup(polygon, resolution=1024):
    """Precompute boundary distance by polar angle around the controller pivot."""
    distances = []
    for index in range(resolution):
        angle = math.pi * 2.0 * float(index) / float(resolution)
        dx, dz = math.cos(angle), math.sin(angle)
        nearest = None
        for segment_index in range(len(polygon)):
            px, pz = polygon[segment_index]
            qx, qz = polygon[(segment_index + 1) % len(polygon)]
            sx, sz = qx - px, qz - pz
            denominator = dx * sz - dz * sx
            if abs(denominator) < 1.0e-12:
                continue
            ray_distance = (px * sz - pz * sx) / denominator
            segment_fraction = (px * dz - pz * dx) / denominator
            if (
                ray_distance > 0.0
                and -1.0e-6 <= segment_fraction <= 1.000001
                and (nearest is None or ray_distance < nearest)
            ):
                nearest = ray_distance
        distances.append(nearest or 1.0e-8)
    return distances


def _footprint_fraction(x, z, lookup):
    """Return radial footprint fraction with constant-time table interpolation."""
    distance = math.sqrt(x * x + z * z)
    if distance < 1.0e-12:
        return 0.0
    angle = math.atan2(z, x)
    if angle < 0.0:
        angle += math.pi * 2.0
    table_position = angle / (math.pi * 2.0) * len(lookup)
    lower = int(math.floor(table_position)) % len(lookup)
    upper = (lower + 1) % len(lookup)
    blend = table_position - math.floor(table_position)
    boundary = lookup[lower] + (lookup[upper] - lookup[lower]) * blend
    return distance / max(boundary, 1.0e-12)


def _build_falloff_lookup(profile_samples, resolution=1025):
    return [
        _profile_value(float(index) / float(resolution - 1), profile_samples)
        for index in range(resolution)
    ]


def _lookup_unit_interval(values, position):
    position = max(0.0, min(float(position), 1.0))
    table_position = position * (len(values) - 1)
    lower = int(math.floor(table_position))
    upper = min(lower + 1, len(values) - 1)
    blend = table_position - lower
    return values[lower] + (values[upper] - values[lower]) * blend


def _randomized_normal(surface_normal, maximum_degrees, rng):
    """Tilt a normal within a cone while retaining full random yaw later."""
    normal = om.MVector(surface_normal).normalize()
    if maximum_degrees <= 0.0:
        return normal
    reference = om.MVector(1, 0, 0) if abs(normal.x) < 0.9 else om.MVector(0, 0, 1)
    tangent = (reference ^ normal).normalize()
    bitangent = (normal ^ tangent).normalize()
    azimuth = rng.uniform(0.0, math.pi * 2.0)
    # sqrt produces an even distribution over the small-angle tilt disk.
    tilt = math.radians(maximum_degrees) * math.sqrt(rng.random())
    direction = tangent * math.cos(azimuth) + bitangent * math.sin(azimuth)
    return (normal * math.cos(tilt) + direction * math.sin(tilt)).normalize()


def _oriented_grain_axes(normal, yaw):
    normal = om.MVector(normal).normalize()
    reference = om.MVector(1, 0, 0) if abs(normal.x) < 0.9 else om.MVector(0, 0, 1)
    tangent = (reference ^ normal).normalize()
    bitangent = (normal ^ tangent).normalize()
    cosine, sine = math.cos(yaw), math.sin(yaw)
    axis_x = tangent * cosine + bitangent * sine
    axis_z = tangent * -sine + bitangent * cosine
    return axis_x.normalize(), normal, axis_z.normalize()


def _ellipsoid_support_radius(direction, axes, radii):
    """Radius of an oriented ellipsoid along a world-space direction."""
    direction = om.MVector(direction).normalize()
    return math.sqrt(
        sum(
            (radius * (axis * direction)) ** 2
            for axis, radius in zip(axes, radii)
        )
    )


def _icosahedron():
    phi = (1.0 + math.sqrt(5.0)) * 0.5
    vertices = [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
    ]
    vertices = [
        tuple(component / math.sqrt(sum(v * v for v in vertex)) for component in vertex)
        for vertex in vertices
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    return vertices, faces


def _octahedron():
    vertices = [
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
        (-1.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, -1.0, 0.0),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1),
        (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4),
    ]
    return vertices, faces


def _grain_polyhedron(high_detail):
    return _icosahedron() if high_detail else _octahedron()


def _make_material():
    if not cmds.objExists(MATERIAL):
        cmds.shadingNode("lambert", asShader=True, name=MATERIAL)
        cmds.setAttr(MATERIAL + ".color", 0.48, 0.29, 0.11, type="double3")
        cmds.setAttr(MATERIAL + ".diffuse", 0.8)
    if not cmds.objExists(SHADING_GROUP):
        cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=SHADING_GROUP)
        cmds.connectAttr(MATERIAL + ".outColor", SHADING_GROUP + ".surfaceShader", force=True)


def _build_mesh(grains, high_detail, soften_edges, progress):
    base_vertices, base_faces = _grain_polyhedron(high_detail)
    vertices = []
    counts = []
    connections = []
    progress.begin_phase(len(grains) + 3, "Preparing grain mesh...")
    update_interval = max(len(grains) // 100, 1)

    for grain_index, grain in enumerate(grains, start=1):
        center, normal, radius, flattening, yaw, stretch = grain
        axis_x, normal, axis_z = _oriented_grain_axes(normal, yaw)
        rx = radius * stretch
        ry = radius * flattening
        rz = radius / stretch
        offset = len(vertices)
        for vx, vy, vz in base_vertices:
            point = center + axis_x * (vx * rx) + normal * (vy * ry) + axis_z * (vz * rz)
            vertices.append(om.MPoint(point))
        for face in base_faces:
            counts.append(3)
            connections.extend(offset + index for index in face)
        if grain_index % update_interval == 0 or grain_index == len(grains):
            progress.update(
                grain_index,
                "Preparing grain geometry: {:,} of {:,}".format(
                    grain_index, len(grains)
                ),
            )
            if progress.cancel_requested(force=True):
                progress.close()
                return None

    if cmds.objExists(OUTPUT_GEO):
        cmds.delete(OUTPUT_GEO)

    progress.update(len(grains) + 1, "Creating combined Maya mesh...")
    # Creating the transform explicitly avoids a Maya-version-dependent return
    # value from MFnMesh.create() when no parent is supplied.
    transform_object = om.MFnTransform().create()
    transform_fn = om.MFnDagNode(transform_object)
    transform_fn.setName(OUTPUT_GEO)
    mesh_fn = om.MFnMesh()
    mesh_fn.create(vertices, counts, connections, parent=transform_object)
    mesh_fn.setName(OUTPUT_GEO + "Shape")
    output = om.MFnDagNode(transform_object).fullPathName()

    if soften_edges:
        progress.update(len(grains) + 2, "Softening grain edges...")
        cmds.polySoftEdge(output, angle=38, constructionHistory=False)
    _make_material()
    cmds.sets(output, edit=True, forceElement=SHADING_GROUP)
    progress.update(len(grains) + 3, "Sand heap complete")
    progress.close()
    return output


def _mark_frontier_failure(frontier, index, retire_after=6):
    """Age a placement site and remove it after repeated local failures."""
    site = frontier[index]
    site["failures"] += 1
    if site["failures"] < retire_after:
        return
    frontier[index] = frontier[-1]
    frontier.pop()


def build_sand_heap():
    selected_transform, selected_shape = _mesh_from_selection()
    remembered_transform, remembered_shape = _remembered_mesh()
    target_transform = selected_transform or remembered_transform
    target_shape = selected_shape or remembered_shape
    if not target_shape:
        raise RuntimeError(
            "Select one polygon-mesh target before the first run, then run the script again."
        )

    _ensure_controls(target_transform)
    if selected_transform:
        _connect_target(selected_transform)

    count = int(cmds.getAttr(SIZE_CTRL + ".grainCount"))
    heap_height = float(cmds.getAttr(SIZE_CTRL + ".heapHeight"))
    base_grain_size = float(cmds.getAttr(SIZE_CTRL + ".grainSize"))
    size_variation = float(cmds.getAttr(SIZE_CTRL + ".grainSizeVariation"))
    flattening = float(cmds.getAttr(SIZE_CTRL + ".grainFlattening"))
    rotation_variance = float(cmds.getAttr(SIZE_CTRL + ".rotationVariance"))
    falloff_power = float(cmds.getAttr(SIZE_CTRL + ".falloffPower"))
    seed = int(cmds.getAttr(SIZE_CTRL + ".seed"))
    auto_increment_seed = bool(cmds.getAttr(SIZE_CTRL + ".autoIncrementSeed"))
    max_failed_placements = int(
        cmds.getAttr(SIZE_CTRL + ".maxFailedPlacements")
    )
    use_projection_cache = bool(cmds.getAttr(SIZE_CTRL + ".useProjectionCache"))
    packing_tightness = float(cmds.getAttr(SIZE_CTRL + ".packingTightness"))
    high_detail = bool(cmds.getAttr(SIZE_CTRL + ".highDetailGrains"))
    soften_edges = bool(cmds.getAttr(SIZE_CTRL + ".softEdges"))
    show_progress = bool(cmds.getAttr(SIZE_CTRL + ".showProgress"))
    rng = random.Random(seed)
    progress = _ProgressWindow(show_progress)

    try:
        progress.begin_phase(2, "Sampling controller curves...")
        footprint_points = _curve_points(SIZE_CTRL, 260)
        footprint = [(point.x, point.z) for point in footprint_points]
        if not _point_in_polygon(0.0, 0.0, footprint):
            raise RuntimeError(
                "The size controller's pivot/local origin must remain inside its curve."
            )
        footprint_lookup = _build_footprint_lookup(footprint)
        progress.update(1, "Sampling falloff curve...")
        falloff_lookup = _build_falloff_lookup(_profile_samples())
        progress.update(2, "Controller lookups ready")
        if progress.cancel_requested(force=True):
            om.MGlobal.displayWarning("Sand heap rebuild cancelled; existing output kept.")
            return None

        min_x = min(point[0] for point in footprint)
        max_x = max(point[0] for point in footprint)
        min_z = min(point[1] for point in footprint)
        max_z = max(point[1] for point in footprint)

        world_matrix = om.MMatrix(cmds.getAttr(SIZE_CTRL + ".worldMatrix[0]"))
        controller_x_vector = om.MVector(1, 0, 0) * world_matrix
        controller_z_vector = om.MVector(0, 0, 1) * world_matrix
        scale_x = max(controller_x_vector.length(), 1.0e-8)
        scale_z = max(controller_z_vector.length(), 1.0e-8)
        horizontal_scale = math.sqrt(scale_x * scale_z)
        grain_size = base_grain_size * horizontal_scale
        controller_up = (om.MVector(0, 1, 0) * world_matrix).normalize()
        controller_u = om.MVector(controller_x_vector)
        controller_u -= controller_up * (controller_u * controller_up)
        if controller_u.length() < 1.0e-8:
            controller_u = om.MVector(0, 0, 1) ^ controller_up
        controller_u.normalize()
        controller_v = (controller_up ^ controller_u).normalize()

        mesh_fn = om.MFnMesh(_dag_path(target_shape))
        accel = mesh_fn.autoUniformGridParams()
        target_bbox = cmds.exactWorldBoundingBox(target_transform)
        diagonal = math.sqrt(
            (target_bbox[3] - target_bbox[0]) ** 2
            + (target_bbox[4] - target_bbox[1]) ** 2
            + (target_bbox[5] - target_bbox[2]) ** 2
        )
        ray_offset = diagonal + heap_height + grain_size * 10.0 + 10.0
        max_ray_distance = ray_offset * 2.5

        max_radius = grain_size * (1.0 + size_variation)
        max_horizontal_radius = max_radius * max(
            1.28, 1.0 / 0.78, flattening
        )
        cell_size = max(max_horizontal_radius * 2.0, 1.0e-6)
        projection_cell_size = max(grain_size * 1.25, 1.0e-6)
        base_vertices, _ = _grain_polyhedron(high_detail)
        axis_extents = tuple(
            max(abs(vertex[axis]) for vertex in base_vertices)
            for axis in range(3)
        )
        vertical_extent = axis_extents[1]
        minimum_vertical_radius = (
            grain_size * (1.0 - size_variation) * flattening * vertical_extent
        )

        # Build reusable candidate sites at roughly one maximum grain diameter
        # apart. Sites remain active while grains can still be supported below
        # their local falloff ceiling.
        placement_spacing = max(grain_size * 1.55, 1.0e-6)
        step_x = placement_spacing / scale_x
        step_z = placement_spacing / scale_z
        column_count = max(1, int(math.ceil((max_x - min_x) / step_x)))
        row_count = max(1, int(math.ceil((max_z - min_z) / step_z)))
        maximum_sites = max(min(count * 2, 20000), 128)
        potential_sites = column_count * row_count
        if potential_sites > maximum_sites:
            spacing_multiplier = math.sqrt(
                float(potential_sites) / float(maximum_sites)
            )
            step_x *= spacing_multiplier
            step_z *= spacing_multiplier
            column_count = max(1, int(math.ceil((max_x - min_x) / step_x)))
            row_count = max(1, int(math.ceil((max_z - min_z) / step_z)))

        frontier = []
        progress.begin_phase(row_count, "Preparing active placement sites...")
        site_update_interval = max(row_count // 100, 1)
        for row in range(row_count):
            z = min_z + (row + 0.5) * step_z
            for column in range(column_count):
                x = min_x + (column + 0.5) * step_x
                radius_fraction = _footprint_fraction(x, z, footprint_lookup)
                if radius_fraction > 1.0:
                    continue
                relative_height = max(
                    0.0, _lookup_unit_interval(falloff_lookup, radius_fraction)
                ) ** falloff_power
                if heap_height * relative_height < minimum_vertical_radius * 2.0:
                    continue
                frontier.append(
                    {
                        "x": x,
                        "z": z,
                        "relative_height": relative_height,
                        "failures": 0,
                    }
                )
            if (row + 1) % site_update_interval == 0 or row + 1 == row_count:
                progress.update(
                    row + 1,
                    "Preparing placement row {:,} of {:,}".format(
                        row + 1, row_count
                    ),
                )
                if progress.cancel_requested(force=True):
                    om.MGlobal.displayWarning(
                        "Sand heap rebuild cancelled; existing output kept."
                    )
                    return None

        if not frontier:
            raise RuntimeError(
                "The footprint/falloff has no room for the current grain size. "
                "Increase heap height or reduce grain size."
            )
        rng.shuffle(frontier)

        grains = []
        spatial_hash = {}
        projection_cache = {}
        cache_miss = object()
        attempts = 0
        raycasts = 0
        cache_hits = 0
        consecutive_failures = 0
        max_total_attempts = max(count * 40 + len(frontier) * 10, 10000)
        progress.begin_phase(count, "Depositing supported grains...")
        progress_update_interval = max(count // 200, 1)
        last_progress_update = time.perf_counter()

        while (
            len(grains) < count
            and frontier
            and consecutive_failures < max_failed_placements
            and attempts < max_total_attempts
        ):
            attempts += 1
            if attempts % 64 == 0 and progress.cancel_requested():
                om.MGlobal.displayWarning(
                    "Sand heap rebuild cancelled; existing output kept."
                )
                return None

            # Two-choice weighted selection favors tall central sites without a
            # global rejection loop or an expensive mutable weighted index.
            site_index = rng.randrange(len(frontier))
            if len(frontier) > 1:
                alternate_index = rng.randrange(len(frontier))
                first_score = frontier[site_index]["relative_height"] * rng.random()
                alternate_score = (
                    frontier[alternate_index]["relative_height"] * rng.random()
                )
                if alternate_score > first_score:
                    site_index = alternate_index
            site = frontier[site_index]
            x = site["x"] + rng.uniform(-0.22, 0.22) * step_x
            z = site["z"] + rng.uniform(-0.22, 0.22) * step_z
            radius_fraction = _footprint_fraction(x, z, footprint_lookup)
            if radius_fraction > 1.0:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=8)
                continue
            relative_height = max(
                0.0, _lookup_unit_interval(falloff_lookup, radius_fraction)
            ) ** falloff_power
            local_height = heap_height * relative_height
            if local_height < minimum_vertical_radius * 2.0:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=4)
                continue

            plane_point = om.MPoint(x, 0.0, z) * world_matrix
            point_vector = om.MVector(plane_point.x, plane_point.y, plane_point.z)
            horizontal_u = point_vector * controller_u
            horizontal_v = point_vector * controller_v
            cell = (
                int(math.floor(horizontal_u / cell_size)),
                int(math.floor(horizontal_v / cell_size)),
            )
            projection_cell = (
                int(math.floor(horizontal_u / projection_cell_size)),
                int(math.floor(horizontal_v / projection_cell_size)),
            )

            cached_projection = (
                projection_cache.get(projection_cell, cache_miss)
                if use_projection_cache
                else cache_miss
            )
            if cached_projection is cache_miss:
                ray_source = plane_point + controller_up * ray_offset
                raycasts += 1
                try:
                    hit = mesh_fn.closestIntersection(
                        om.MFloatPoint(ray_source),
                        om.MFloatVector(-controller_up),
                        om.MSpace.kWorld,
                        max_ray_distance,
                        False,
                        accelParams=accel,
                    )
                except RuntimeError:
                    hit = None
                if not hit:
                    cached_projection = None
                else:
                    hit_point = om.MPoint(hit[0])
                    face_id = hit[2]
                    try:
                        surface_normal, _ = mesh_fn.getClosestNormal(
                            hit_point, om.MSpace.kWorld
                        )
                    except (RuntimeError, TypeError):
                        surface_normal = mesh_fn.getPolygonNormal(
                            face_id, om.MSpace.kWorld
                        )
                    surface_normal = om.MVector(surface_normal).normalize()
                    if surface_normal * controller_up < 0.0:
                        surface_normal *= -1.0
                    cached_projection = (
                        (hit_point.x, hit_point.y, hit_point.z),
                        (surface_normal.x, surface_normal.y, surface_normal.z),
                    )
                if use_projection_cache:
                    projection_cache[projection_cell] = cached_projection
            else:
                cache_hits += 1

            if cached_projection is None:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=3)
                continue

            sample_point_components, normal_components = cached_projection
            sample_point = om.MPoint(*sample_point_components)
            surface_normal = om.MVector(*normal_components).normalize()
            normal_up = surface_normal * controller_up
            if normal_up < 1.0e-5:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=3)
                continue
            sample_delta = sample_point - plane_point
            plane_parameter = (om.MVector(sample_delta) * surface_normal) / normal_up
            hit_point = plane_point + controller_up * plane_parameter
            hit_vector = om.MVector(hit_point.x, hit_point.y, hit_point.z)
            ground_elevation = hit_vector * controller_up

            radius = grain_size * rng.uniform(
                1.0 - size_variation, 1.0 + size_variation
            )
            yaw = rng.uniform(0.0, math.pi * 2.0)
            stretch = rng.uniform(0.78, 1.28)
            grain_normal = _randomized_normal(
                surface_normal, rotation_variance, rng
            )
            axes = _oriented_grain_axes(grain_normal, yaw)
            radii = (
                radius * stretch * axis_extents[0],
                radius * flattening * axis_extents[1],
                radius / stretch * axis_extents[2],
            )
            vertical_support = _ellipsoid_support_radius(
                controller_up, axes, radii
            )
            terrain_support = _ellipsoid_support_radius(
                surface_normal, axes, radii
            )
            support_elevation = ground_elevation + (
                terrain_support * min(packing_tightness, 1.0) / normal_up
            )

            for cell_u in range(cell[0] - 1, cell[0] + 2):
                for cell_v in range(cell[1] - 1, cell[1] + 2):
                    for neighbor in spatial_hash.get((cell_u, cell_v), []):
                        delta_u = horizontal_u - neighbor["u"]
                        delta_v = horizontal_v - neighbor["v"]
                        distance_sq = delta_u * delta_u + delta_v * delta_v
                        if distance_sq < 1.0e-12:
                            horizontal_direction = controller_u
                        else:
                            inverse_distance = 1.0 / math.sqrt(distance_sq)
                            horizontal_direction = (
                                controller_u * (delta_u * inverse_distance)
                                + controller_v * (delta_v * inverse_distance)
                            )
                        combined_horizontal = _ellipsoid_support_radius(
                            horizontal_direction, axes, radii
                        ) + _ellipsoid_support_radius(
                            horizontal_direction,
                            neighbor["axes"],
                            neighbor["radii"],
                        )
                        if distance_sq >= combined_horizontal * combined_horizontal:
                            continue
                        contact = math.sqrt(
                            max(
                                0.0,
                                1.0
                                - distance_sq / (combined_horizontal ** 2),
                            )
                        )
                        neighbor_support = neighbor["center_elevation"] + (
                            vertical_support + neighbor["vertical_support"]
                        ) * contact * packing_tightness
                        support_elevation = max(
                            support_elevation, neighbor_support
                        )

            supported_top = (
                support_elevation - ground_elevation
                + vertical_support
            )
            if supported_top > local_height:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=5)
                continue

            center = hit_point + controller_up * (
                support_elevation - ground_elevation
            )
            grains.append(
                (center, grain_normal, radius, flattening, yaw, stretch)
            )
            spatial_hash.setdefault(cell, []).append(
                {
                    "u": horizontal_u,
                    "v": horizontal_v,
                    "center_elevation": support_elevation,
                    "vertical_support": vertical_support,
                    "axes": axes,
                    "radii": radii,
                }
            )
            site["failures"] = 0
            consecutive_failures = 0

            now = time.perf_counter()
            if (
                len(grains) % progress_update_interval == 0
                or len(grains) == count
                or now - last_progress_update >= 0.15
            ):
                progress.update(
                    len(grains),
                    "Depositing grains: {:,} of {:,} | {:,} rays | {:,} cached".format(
                        len(grains), count, raycasts, cache_hits
                    ),
                )
                last_progress_update = now

        progress.close()
        if not grains:
            raise RuntimeError(
                "No rays from the size controller hit usable target surface, or "
                "the falloff has no supported capacity for the current grain size."
            )

        output = _build_mesh(
            grains,
            high_detail=high_detail,
            soften_edges=soften_edges,
            progress=progress,
        )
        if output is None:
            om.MGlobal.displayWarning(
                "Sand heap rebuild cancelled; existing output kept."
            )
            return None

        next_seed = seed
        if auto_increment_seed:
            next_seed = 0 if seed >= 2147483647 else seed + 1
            cmds.setAttr(SIZE_CTRL + ".seed", next_seed)
            try:
                if cmds.intSliderGrp(SEED_SLIDER, exists=True):
                    cmds.intSliderGrp(SEED_SLIDER, edit=True, value=next_seed)
            except RuntimeError:
                # The scene attribute is authoritative if the old UI vanished.
                pass

        cmds.select(SIZE_CTRL, replace=True)
        message = (
            "Built {:,} grains on {} with seed {}; {:,} raycasts and {:,} cached projections"
        ).format(len(grains), target_transform, seed, raycasts, cache_hits)
        if auto_increment_seed:
            message += "; next seed is {}".format(next_seed)
        if len(grains) < count:
            message += (
                " (requested {:,}; active supported capacity was exhausted)"
            ).format(count)
        om.MGlobal.displayInfo(message)
        return output
    finally:
        progress.close()


def _set_grain_size(value):
    if cmds.objExists(SIZE_CTRL + ".grainSize"):
        cmds.setAttr(SIZE_CTRL + ".grainSize", max(float(value), 0.001))


def _set_grain_count(value):
    if cmds.objExists(SIZE_CTRL + ".grainCount"):
        cmds.setAttr(
            SIZE_CTRL + ".grainCount",
            max(100, min(int(value), 100000)),
        )


def _set_size_variance(value):
    if cmds.objExists(SIZE_CTRL + ".grainSizeVariation"):
        cmds.setAttr(
            SIZE_CTRL + ".grainSizeVariation",
            max(0.0, min(float(value), 0.95)),
        )


def _set_rotation_variance(value):
    if cmds.objExists(SIZE_CTRL + ".rotationVariance"):
        cmds.setAttr(
            SIZE_CTRL + ".rotationVariance",
            max(0.0, min(float(value), 90.0)),
        )


def _set_seed(value):
    if cmds.objExists(SIZE_CTRL + ".seed"):
        cmds.setAttr(
            SIZE_CTRL + ".seed",
            max(0, min(int(value), 2147483647)),
        )


def _set_auto_increment_seed(value):
    if cmds.objExists(SIZE_CTRL + ".autoIncrementSeed"):
        cmds.setAttr(SIZE_CTRL + ".autoIncrementSeed", bool(value))


def _set_failure_limit(value):
    if cmds.objExists(SIZE_CTRL + ".maxFailedPlacements"):
        cmds.setAttr(
            SIZE_CTRL + ".maxFailedPlacements",
            max(100, int(value)),
        )


def _set_packing_tightness(value):
    if cmds.objExists(SIZE_CTRL + ".packingTightness"):
        cmds.setAttr(
            SIZE_CTRL + ".packingTightness",
            max(0.80, min(float(value), 1.05)),
        )


def _set_projection_cache(value):
    if cmds.objExists(SIZE_CTRL + ".useProjectionCache"):
        cmds.setAttr(SIZE_CTRL + ".useProjectionCache", bool(value))


def _set_high_detail(value):
    if cmds.objExists(SIZE_CTRL + ".highDetailGrains"):
        cmds.setAttr(SIZE_CTRL + ".highDetailGrains", bool(value))


def _set_soft_edges(value):
    if cmds.objExists(SIZE_CTRL + ".softEdges"):
        cmds.setAttr(SIZE_CTRL + ".softEdges", bool(value))


def _set_show_progress(value):
    if cmds.objExists(SIZE_CTRL + ".showProgress"):
        cmds.setAttr(SIZE_CTRL + ".showProgress", bool(value))


def _rebuild_from_controls(*_):
    cmds.undoInfo(openChunk=True, chunkName="Rebuild Sand Heap")
    try:
        build_sand_heap()
    finally:
        cmds.undoInfo(closeChunk=True)


def _show_control_window():
    if cmds.window(CONTROL_WINDOW, exists=True):
        cmds.deleteUI(CONTROL_WINDOW, window=True)

    grain_count = int(cmds.getAttr(SIZE_CTRL + ".grainCount"))
    grain_size = float(cmds.getAttr(SIZE_CTRL + ".grainSize"))
    size_variance = float(cmds.getAttr(SIZE_CTRL + ".grainSizeVariation"))
    rotation_variance = float(cmds.getAttr(SIZE_CTRL + ".rotationVariance"))
    seed = int(cmds.getAttr(SIZE_CTRL + ".seed"))
    auto_increment_seed = bool(
        cmds.getAttr(SIZE_CTRL + ".autoIncrementSeed")
    )
    failure_limit = int(cmds.getAttr(SIZE_CTRL + ".maxFailedPlacements"))
    packing_tightness = float(cmds.getAttr(SIZE_CTRL + ".packingTightness"))
    use_projection_cache = bool(cmds.getAttr(SIZE_CTRL + ".useProjectionCache"))
    high_detail = bool(cmds.getAttr(SIZE_CTRL + ".highDetailGrains"))
    soften_edges = bool(cmds.getAttr(SIZE_CTRL + ".softEdges"))
    show_progress = bool(cmds.getAttr(SIZE_CTRL + ".showProgress"))
    slider_max = max(grain_size * 4.0, 0.1)

    window = cmds.window(
        CONTROL_WINDOW,
        title="Sand Heap Controls",
        sizeable=True,
        widthHeight=(420, 580),
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnOffset=("both", 10))
    cmds.text(
        label="Values update the controller; press Rebuild to regenerate the grains.",
        align="left",
    )
    cmds.button(label="Rebuild Sand Heap", height=36, command=_rebuild_from_controls)
    cmds.separator(style="in", height=8)
    cmds.intSliderGrp(
        label="Number of Grains",
        field=True,
        minValue=100,
        maxValue=20000,
        fieldMinValue=100,
        fieldMaxValue=100000,
        value=grain_count,
        step=100,
        dragCommand=_set_grain_count,
        changeCommand=_set_grain_count,
    )
    cmds.floatSliderGrp(
        label="Grain Size",
        field=True,
        minValue=0.001,
        maxValue=slider_max,
        fieldMinValue=0.001,
        fieldMaxValue=10000.0,
        value=grain_size,
        step=max(slider_max / 500.0, 0.001),
        precision=4,
        dragCommand=_set_grain_size,
        changeCommand=_set_grain_size,
    )
    cmds.floatSliderGrp(
        label="Size Variance",
        field=True,
        minValue=0.0,
        maxValue=0.95,
        fieldMinValue=0.0,
        fieldMaxValue=0.95,
        value=size_variance,
        step=0.01,
        precision=3,
        dragCommand=_set_size_variance,
        changeCommand=_set_size_variance,
    )
    cmds.floatSliderGrp(
        label="Rotation Variance",
        field=True,
        minValue=0.0,
        maxValue=90.0,
        fieldMinValue=0.0,
        fieldMaxValue=90.0,
        value=rotation_variance,
        step=1.0,
        precision=1,
        dragCommand=_set_rotation_variance,
        changeCommand=_set_rotation_variance,
    )
    cmds.intSliderGrp(
        SEED_SLIDER,
        label="Seed",
        field=True,
        minValue=0,
        maxValue=1000000,
        fieldMinValue=0,
        fieldMaxValue=2147483647,
        value=seed,
        step=1,
        dragCommand=_set_seed,
        changeCommand=_set_seed,
    )
    cmds.checkBox(
        label="Auto-increment seed after successful rebuild",
        value=auto_increment_seed,
        changeCommand=_set_auto_increment_seed,
    )
    cmds.intSliderGrp(
        label="Failure Limit",
        field=True,
        minValue=100,
        maxValue=20000,
        fieldMinValue=100,
        fieldMaxValue=1000000,
        value=failure_limit,
        step=100,
        dragCommand=_set_failure_limit,
        changeCommand=_set_failure_limit,
    )
    cmds.floatSliderGrp(
        label="Packing",
        field=True,
        minValue=0.80,
        maxValue=1.05,
        fieldMinValue=0.80,
        fieldMaxValue=1.05,
        value=packing_tightness,
        step=0.01,
        precision=2,
        dragCommand=_set_packing_tightness,
        changeCommand=_set_packing_tightness,
    )
    cmds.separator(style="in", height=8)
    cmds.checkBox(
        label="Cache terrain projections (faster)",
        value=use_projection_cache,
        changeCommand=_set_projection_cache,
    )
    cmds.checkBox(
        label="High-detail grains (20 faces instead of 8)",
        value=high_detail,
        changeCommand=_set_high_detail,
    )
    cmds.checkBox(
        label="Soften grain edges",
        value=soften_edges,
        changeCommand=_set_soft_edges,
    )
    cmds.checkBox(
        label="Show interruptible progress windows",
        value=show_progress,
        changeCommand=_set_show_progress,
    )
    cmds.text(
        label="Rotation variance is maximum tilt from the target surface normal.",
        align="left",
    )
    cmds.showWindow(window)


cmds.undoInfo(openChunk=True, chunkName="Build Sand Heap")
try:
    build_sand_heap()
finally:
    cmds.undoInfo(closeChunk=True)

_show_control_window()
