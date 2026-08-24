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
    _add_attr(SIZE_CTRL, "radialDensityFalloff", "double", 1.0, 0.0)
    # Older versions imposed an arbitrary maximum of 8. Remove it in-place so
    # existing scene controllers gain the uncapped behavior too.
    try:
        cmds.addAttr(
            SIZE_CTRL + ".radialDensityFalloff",
            edit=True,
            hasMaxValue=False,
        )
    except RuntimeError:
        pass
    density_controls_upgrade = not cmds.objExists(
        SIZE_CTRL + ".radialDensityStrength"
    )
    _add_attr(SIZE_CTRL, "radialDensityStrength", "double", 0.75, 0.0, 1.0)
    _add_attr(SIZE_CTRL, "radialDensityFadeStart", "double", 0.40, 0.0, 0.95)
    _add_attr(SIZE_CTRL, "radialDensityFadeShape", "double", 1.0, 0.25, 4.0)
    if density_controls_upgrade and not created_size:
        old_falloff = max(
            float(cmds.getAttr(SIZE_CTRL + ".radialDensityFalloff")), 0.0
        )
        migrated_strength = (
            0.0 if old_falloff <= 0.0 else old_falloff / (old_falloff + 0.5)
        )
        cmds.setAttr(
            SIZE_CTRL + ".radialDensityStrength",
            min(migrated_strength, 1.0),
        )
    try:
        cmds.setAttr(
            SIZE_CTRL + ".radialDensityFalloff",
            keyable=False,
            channelBox=False,
        )
    except RuntimeError:
        pass
    _add_attr(SIZE_CTRL, "falloffPower", "double", 1.0, 0.05, 20.0)
    _add_attr(SIZE_CTRL, "seed", "long", 12345, 0, 2147483647)
    _add_attr(SIZE_CTRL, "autoIncrementSeed", "bool", 1)
    _add_attr(SIZE_CTRL, "maxFailedPlacements", "long", 3000, 100, 1000000)
    _add_attr(SIZE_CTRL, "useProjectionCache", "bool", 1)
    quality_upgrade = not cmds.objExists(SIZE_CTRL + ".packingTightness")
    _add_attr(SIZE_CTRL, "packingTightness", "double", 0.94, 0.80, 1.05)
    _add_attr(SIZE_CTRL, "settlingIterations", "long", 5, 0, 20)
    _add_attr(SIZE_CTRL, "settlingRadius", "double", 1.25, 0.0, 4.0)
    _add_attr(SIZE_CTRL, "maxSupportSlope", "double", 60.0, 0.0, 89.0)
    _add_attr(SIZE_CTRL, "spillBalance", "double", 0.60, 0.0, 1.0)
    _add_attr(SIZE_CTRL, "useWorldGravity", "bool", 1)
    _add_attr(SIZE_CTRL, "proposalBatchSize", "long", 4, 1, 16)
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
    radial_density_strength = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityStrength")
    )
    radial_density_fade_start = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityFadeStart")
    )
    radial_density_fade_shape = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityFadeShape")
    )
    falloff_power = float(cmds.getAttr(SIZE_CTRL + ".falloffPower"))
    seed = int(cmds.getAttr(SIZE_CTRL + ".seed"))
    auto_increment_seed = bool(cmds.getAttr(SIZE_CTRL + ".autoIncrementSeed"))
    max_failed_placements = int(
        cmds.getAttr(SIZE_CTRL + ".maxFailedPlacements")
    )
    use_projection_cache = bool(cmds.getAttr(SIZE_CTRL + ".useProjectionCache"))
    packing_tightness = float(cmds.getAttr(SIZE_CTRL + ".packingTightness"))
    settling_iterations = int(cmds.getAttr(SIZE_CTRL + ".settlingIterations"))
    settling_radius = float(cmds.getAttr(SIZE_CTRL + ".settlingRadius"))
    max_support_slope = float(cmds.getAttr(SIZE_CTRL + ".maxSupportSlope"))
    spill_balance = float(cmds.getAttr(SIZE_CTRL + ".spillBalance"))
    use_world_gravity = bool(cmds.getAttr(SIZE_CTRL + ".useWorldGravity"))
    proposal_batch_size = int(cmds.getAttr(SIZE_CTRL + ".proposalBatchSize"))
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
        projection_up = (om.MVector(0, 1, 0) * world_matrix).normalize()
        gravity_up = (
            om.MVector(0, 1, 0)
            if use_world_gravity
            else om.MVector(projection_up)
        )
        controller_u = om.MVector(controller_x_vector)
        controller_u -= gravity_up * (controller_u * gravity_up)
        if controller_u.length() < 1.0e-8:
            controller_u = om.MVector(0, 0, 1) ^ gravity_up
        controller_u.normalize()
        controller_v = (gravity_up ^ controller_u).normalize()

        mesh_fn = om.MFnMesh(_dag_path(target_shape))
        accel = mesh_fn.autoUniformGridParams()
        try:
            target_shell_count = int(
                cmds.polyEvaluate(target_shape, shell=True) or 1
            )
        except RuntimeError:
            target_shell_count = 1
        composite_target = target_shell_count > 8
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
        cache_reuse_radius_sq = (grain_size * 0.45) ** 2
        minimum_support_normal = math.cos(math.radians(max_support_slope))
        base_vertices, _ = _grain_polyhedron(high_detail)
        axis_extents = tuple(
            max(abs(vertex[axis]) for vertex in base_vertices)
            for axis in range(3)
        )
        inverse_axis_extents = tuple(1.0 / extent for extent in axis_extents)
        vertical_extent = axis_extents[1]
        minimum_vertical_radius = (
            grain_size * (1.0 - size_variation) * flattening * vertical_extent
        )

        # Build a globally distributed blue-noise field in the controller plane.
        # Density and supported layer quotas are assigned only after the field
        # exists, keeping sampling, density, and physical capacity independent.
        placement_spacing = max(grain_size * 1.25, 1.0e-6)
        poisson_cell_size = placement_spacing / math.sqrt(2.0)
        maximum_sites = max(min(count * 2, 20000), 128)
        frontier = []
        poisson_grid = {}
        density_band_count = 64
        minimum_layer_height = max(minimum_vertical_radius * 2.0, 1.0e-8)
        quota_layer_height = max(
            grain_size
            * flattening
            * vertical_extent
            * 2.0
            * min(packing_tightness, 1.0),
            1.0e-8,
        )

        sector_count = 12
        sector_populations = [0] * sector_count

        def radial_sector(x, z):
            angle = math.atan2(z, x)
            if angle < 0.0:
                angle += math.pi * 2.0
            return min(
                int(angle * sector_count / (math.pi * 2.0)),
                sector_count - 1,
            )

        def sector_excess(sector):
            expected = max(float(len(grains)) / sector_count, 1.0)
            return max(0.0, sector_populations[sector] - expected) / expected

        def balance_penalty(x, z):
            return (
                spill_balance
                * grain_size
                * 3.0
                * sector_excess(radial_sector(x, z))
            )

        def density_weight(normalized_radius):
            """Smooth bounded density from the dense core to active edge."""
            if radial_density_strength <= 0.0:
                return 1.0
            if normalized_radius <= radial_density_fade_start:
                return 1.0
            fade_width = max(1.0 - radial_density_fade_start, 1.0e-8)
            blend = min(
                max(
                    (normalized_radius - radial_density_fade_start)
                    / fade_width,
                    0.0,
                ),
                1.0,
            )
            smooth_blend = blend * blend * (3.0 - 2.0 * blend)
            shaped_blend = smooth_blend ** radial_density_fade_shape
            return max(0.0, 1.0 - radial_density_strength * shaped_blend)

        def make_site(x, z):
            if x < min_x or x > max_x or z < min_z or z > max_z:
                return None
            radius_fraction = _footprint_fraction(x, z, footprint_lookup)
            if radius_fraction > 1.0:
                return None
            relative_height = max(
                0.0, _lookup_unit_interval(falloff_lookup, radius_fraction)
            ) ** falloff_power
            local_height = heap_height * relative_height
            if local_height < minimum_layer_height:
                return None
            return {
                "x": x,
                "z": z,
                "site_u": x * scale_x,
                "site_v": z * scale_z,
                "radius_fraction": radius_fraction,
                "layer_capacity": max(
                    int(math.floor(local_height / quota_layer_height)), 1
                ),
                "max_uses": 0,
                "sector": radial_sector(x, z),
                "uses": 0,
                "failures": 0,
            }

        def add_poisson_site(candidate):
            candidate_cell = (
                int(math.floor(candidate["site_u"] / poisson_cell_size)),
                int(math.floor(candidate["site_v"] / poisson_cell_size)),
            )
            separated = True
            for grid_u in range(candidate_cell[0] - 2, candidate_cell[0] + 3):
                for grid_v in range(
                    candidate_cell[1] - 2, candidate_cell[1] + 3
                ):
                    neighbor_index = poisson_grid.get((grid_u, grid_v))
                    if neighbor_index is None:
                        continue
                    neighbor = frontier[neighbor_index]
                    delta_u = candidate["site_u"] - neighbor["site_u"]
                    delta_v = candidate["site_v"] - neighbor["site_v"]
                    if (
                        delta_u * delta_u + delta_v * delta_v
                        < placement_spacing ** 2
                    ):
                        separated = False
                        break
                if not separated:
                    break
            if not separated:
                return False
            frontier.append(candidate)
            new_index = len(frontier) - 1
            poisson_grid[candidate_cell] = new_index
            return True

        center_site = make_site(0.0, 0.0)
        if center_site is not None:
            add_poisson_site(center_site)

        progress.begin_phase(maximum_sites, "Preparing blue-noise placement sites...")
        site_update_interval = max(maximum_sites // 200, 1)
        last_site_progress = 0
        site_attempts = 0
        consecutive_site_failures = 0
        maximum_site_attempts = max(maximum_sites * 50, 10000)
        site_failure_limit = max(min(maximum_sites * 2, 20000), 3000)
        while (
            len(frontier) < maximum_sites
            and site_attempts < maximum_site_attempts
            and consecutive_site_failures < site_failure_limit
        ):
            site_attempts += 1
            candidate = make_site(
                rng.uniform(min_x, max_x),
                rng.uniform(min_z, max_z),
            )
            if candidate is not None and add_poisson_site(candidate):
                consecutive_site_failures = 0
            else:
                consecutive_site_failures += 1

            if (
                len(frontier) - last_site_progress >= site_update_interval
                or len(frontier) == maximum_sites
            ):
                progress.update(
                    len(frontier),
                    "Preparing blue-noise sites: {:,} of up to {:,}".format(
                        len(frontier), maximum_sites
                    ),
                )
                last_site_progress = len(frontier)
            if site_attempts % 256 == 0 and progress.cancel_requested():
                om.MGlobal.displayWarning(
                    "Sand heap rebuild cancelled; existing output kept."
                )
                return None

        if not frontier:
            raise RuntimeError(
                "The footprint/profile has no sites with enough height for the "
                "current grain size. Increase heap height or reduce grain size."
            )

        # Aggregate physical capacity into radial bands, then find the smallest
        # occupied extent whose smoothly faded capacity can hold the requested
        # material. Smaller grains at a fixed count therefore make one smaller
        # pile rather than many disconnected islands across the full controller.
        density_bands = [[] for _ in range(density_band_count)]
        band_capacities = [0] * density_band_count
        for site in frontier:
            band_index = min(
                int(site["radius_fraction"] * density_band_count),
                density_band_count - 1,
            )
            site["band"] = band_index
            density_bands[band_index].append(site)
            band_capacities[band_index] += site["layer_capacity"]

        effective_band = density_band_count - 1
        desired_band_capacities = [0.0] * density_band_count
        desired_total = 0.0
        for extent_band in range(density_band_count):
            extent_denominator = max(extent_band, 1)
            trial_capacities = [0.0] * density_band_count
            trial_total = 0.0
            for band_index in range(extent_band + 1):
                normalized_radius = band_index / float(extent_denominator)
                capacity = (
                    band_capacities[band_index]
                    * density_weight(normalized_radius)
                )
                trial_capacities[band_index] = capacity
                trial_total += capacity
            desired_band_capacities = trial_capacities
            desired_total = trial_total
            effective_band = extent_band
            if desired_total >= count:
                break

        if desired_total <= 0.0:
            raise RuntimeError(
                "The density controls leave no active grain capacity. Reduce "
                "Density Strength or move Fade Start outward."
            )

        density_target_count = min(count, int(math.floor(desired_total)))
        if density_target_count < 1:
            density_target_count = 1
        quota_scale = min(density_target_count / desired_total, 1.0)
        raw_quotas = [capacity * quota_scale for capacity in desired_band_capacities]
        band_quotas = [int(math.floor(value)) for value in raw_quotas]
        quota_remainder = density_target_count - sum(band_quotas)
        remainder_bands = [
            band_index
            for band_index in range(effective_band + 1)
            if band_quotas[band_index] < band_capacities[band_index]
            and raw_quotas[band_index] - band_quotas[band_index] > 0.0
        ]
        while quota_remainder > 0 and remainder_bands:
            total_remainder_weight = sum(
                raw_quotas[band_index] - band_quotas[band_index]
                for band_index in remainder_bands
            )
            if total_remainder_weight <= 1.0e-12:
                break
            threshold = rng.random() * total_remainder_weight
            cumulative = 0.0
            chosen_position = 0
            for position, band_index in enumerate(remainder_bands):
                cumulative += raw_quotas[band_index] - band_quotas[band_index]
                if cumulative >= threshold:
                    chosen_position = position
                    break
            chosen_band = remainder_bands.pop(chosen_position)
            band_quotas[chosen_band] += 1
            quota_remainder -= 1

        # Allocate each band's quota one physical layer at a time. Sparse outer
        # bands select isolated blue-noise sites, while dense inner bands fill a
        # complete base layer before receiving stacked uses.
        for band_index in range(effective_band + 1):
            remaining_quota = band_quotas[band_index]
            layer_index = 0
            band_sites = density_bands[band_index]
            while remaining_quota > 0:
                layer_sites = [
                    site
                    for site in band_sites
                    if site["layer_capacity"] > layer_index
                ]
                if not layer_sites:
                    break
                rng.shuffle(layer_sites)
                layer_count = min(remaining_quota, len(layer_sites))
                for site in layer_sites[:layer_count]:
                    site["max_uses"] += 1
                remaining_quota -= layer_count
                layer_index += 1

        effective_radius = max(
            (effective_band + 1) / float(density_band_count),
            1.0 / density_band_count,
        )
        frontier = [site for site in frontier if site["max_uses"] > 0]
        for site in frontier:
            site["active_radius"] = min(
                site["radius_fraction"] / effective_radius, 1.0
            )
        if not frontier:
            raise RuntimeError(
                "The annular density quotas produced no active placement sites."
            )
        rng.shuffle(frontier)

        grains = []
        spatial_hash = {}
        projection_cache = {}
        cache_miss = object()
        placement_stats = {
            "raycasts": 0,
            "forced_raycasts": 0,
            "cache_hits": 0,
            "settled": 0,
            "steep_rejects": 0,
            "exact_rejects": 0,
        }

        def grain_mesh_support_radius(direction, axes, radii):
            """Exact support distance of the rendered grain polyhedron."""
            direction = om.MVector(direction).normalize()
            projections = tuple(axis * direction for axis in axes)
            scales = tuple(
                radius * inverse_extent
                for radius, inverse_extent in zip(
                    radii, inverse_axis_extents
                )
            )
            return max(
                abs(
                    vertex[0] * scales[0] * projections[0]
                    + vertex[1] * scales[1] * projections[1]
                    + vertex[2] * scales[2] * projections[2]
                )
                for vertex in base_vertices
            )

        def project_surface(x, z, force_exact=False):
            """Project a controller-plane point, optionally bypassing the cache."""
            plane_point = om.MPoint(x, 0.0, z) * world_matrix
            point_vector = om.MVector(
                plane_point.x, plane_point.y, plane_point.z
            )
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
            used_cached_projection = False
            if force_exact or not use_projection_cache:
                cached_projection = cache_miss
            else:
                cached_projection = projection_cache.get(
                    projection_cell, cache_miss
                )
                if cached_projection is not cache_miss:
                    sample_u = cached_projection[2]
                    sample_v = cached_projection[3]
                    cache_delta_u = horizontal_u - sample_u
                    cache_delta_v = horizontal_v - sample_v
                    if (
                        cache_delta_u * cache_delta_u
                        + cache_delta_v * cache_delta_v
                        > cache_reuse_radius_sq
                    ):
                        cached_projection = cache_miss
            if cached_projection is cache_miss:
                ray_source = plane_point + projection_up * ray_offset
                placement_stats["raycasts"] += 1
                if force_exact:
                    placement_stats["forced_raycasts"] += 1
                try:
                    hit = mesh_fn.closestIntersection(
                        om.MFloatPoint(ray_source),
                        om.MFloatVector(-projection_up),
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
                    sample_point = om.MPoint(hit[0])
                    face_id = hit[2]
                    try:
                        surface_normal = mesh_fn.getPolygonNormal(
                            face_id, om.MSpace.kWorld
                        )
                    except RuntimeError:
                        surface_normal, _ = mesh_fn.getClosestNormal(
                            sample_point, om.MSpace.kWorld
                        )
                    surface_normal = om.MVector(surface_normal).normalize()
                    if surface_normal * gravity_up < 0.0:
                        surface_normal *= -1.0
                    cached_projection = (
                        (sample_point.x, sample_point.y, sample_point.z),
                        (surface_normal.x, surface_normal.y, surface_normal.z),
                        horizontal_u,
                        horizontal_v,
                    )
                # Do not cache misses: a nearby ray can still hit a small grain
                # or a different shell inside the same projection cell.
                if use_projection_cache and cached_projection is not None:
                    projection_cache[projection_cell] = cached_projection
            else:
                placement_stats["cache_hits"] += 1
                used_cached_projection = True

            if cached_projection is None:
                return None
            sample_components, normal_components, _, _ = cached_projection
            sample_point = om.MPoint(*sample_components)
            surface_normal = om.MVector(*normal_components).normalize()
            projection_dot = surface_normal * projection_up
            normal_up = surface_normal * gravity_up
            if abs(projection_dot) < 1.0e-5:
                if used_cached_projection:
                    return project_surface(x, z, force_exact=True)
                return None
            if normal_up < minimum_support_normal:
                if used_cached_projection:
                    return project_surface(x, z, force_exact=True)
                placement_stats["steep_rejects"] += 1
                return None
            sample_delta = sample_point - plane_point
            plane_parameter = (
                om.MVector(sample_delta) * surface_normal
            ) / projection_dot
            hit_point = plane_point + projection_up * plane_parameter
            hit_vector = om.MVector(hit_point.x, hit_point.y, hit_point.z)
            return {
                "hit_point": hit_point,
                "surface_normal": surface_normal,
                "normal_up": normal_up,
                "ground_elevation": hit_vector * gravity_up,
                "u": horizontal_u,
                "v": horizontal_v,
                "cell": cell,
            }

        def evaluate_drop(x, z, axes, radii, vertical_support, projection=None):
            """Return the lowest collision-free support at a footprint point."""
            radius_fraction = _footprint_fraction(x, z, footprint_lookup)
            if radius_fraction > 1.0:
                return None
            relative_height = max(
                0.0, _lookup_unit_interval(falloff_lookup, radius_fraction)
            ) ** falloff_power
            local_height = heap_height * relative_height
            if local_height < minimum_vertical_radius * 2.0:
                return None
            projection = projection or project_surface(x, z)
            if projection is None:
                return None

            surface_normal = projection["surface_normal"]
            terrain_support = grain_mesh_support_radius(
                surface_normal, axes, radii
            )
            terrain_elevation = projection["ground_elevation"] + (
                terrain_support
                * min(packing_tightness, 1.0)
                / projection["normal_up"]
            )
            constraints = [("terrain", terrain_elevation)]
            cell = projection["cell"]
            for cell_u in range(cell[0] - 1, cell[0] + 2):
                for cell_v in range(cell[1] - 1, cell[1] + 2):
                    for neighbor in spatial_hash.get((cell_u, cell_v), []):
                        delta_u = projection["u"] - neighbor["u"]
                        delta_v = projection["v"] - neighbor["v"]
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
                                1.0 - distance_sq / (combined_horizontal ** 2),
                            )
                        )
                        neighbor_elevation = neighbor["center_elevation"] + (
                            vertical_support + neighbor["vertical_support"]
                        ) * contact * packing_tightness
                        constraints.append(("neighbor", neighbor_elevation))

            support_elevation = max(value for _, value in constraints)
            supported_top = (
                support_elevation - projection["ground_elevation"]
                + vertical_support
            )
            if supported_top > local_height:
                return None
            contact_tolerance = max(grain_size * 0.16, 1.0e-5)
            terrain_contact = support_elevation - terrain_elevation <= contact_tolerance
            neighbor_contacts = sum(
                1
                for kind, value in constraints
                if kind == "neighbor"
                and support_elevation - value <= contact_tolerance
            )
            center = projection["hit_point"] + gravity_up * (
                support_elevation - projection["ground_elevation"]
            )
            return {
                "x": x,
                "z": z,
                "center": center,
                "cell": cell,
                "u": projection["u"],
                "v": projection["v"],
                "center_elevation": support_elevation,
                "terrain_contact": terrain_contact,
                "neighbor_contacts": neighbor_contacts,
                "stable": terrain_contact or neighbor_contacts >= 2,
                "surface_normal": surface_normal,
                "sector": radial_sector(x, z),
            }

        attempts = 0
        consecutive_failures = 0
        max_total_attempts = max(count * 40 + len(frontier) * 10, 10000)
        progress.begin_phase(count, "Dropping and settling grains...")
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

            # Compare a small randomized proposal batch. This reduces the
            # first-mover sensitivity of strictly sequential deposition while
            # retaining the active-frontier solver's speed.
            site_index = rng.randrange(len(frontier))
            best_site_score = -1.0
            for _ in range(min(proposal_batch_size, len(frontier))):
                proposal_index = rng.randrange(len(frontier))
                proposal = frontier[proposal_index]
                balance_weight = 1.0 / (
                    1.0
                    + spill_balance
                    * 4.0
                    * sector_excess(proposal["sector"])
                )
                proposal_score = (
                    balance_weight
                    * rng.random()
                    / (
                        ((1.0 + proposal["uses"]) ** 1.5)
                        * (1.0 + proposal["active_radius"] * 2.0)
                    )
                )
                if proposal_score > best_site_score:
                    site_index = proposal_index
                    best_site_score = proposal_score
            site = frontier[site_index]
            jitter_angle = rng.uniform(0.0, math.pi * 2.0)
            jitter_distance = (
                placement_spacing * 0.45 * math.sqrt(rng.random())
            )
            x = site["x"] + math.cos(jitter_angle) * jitter_distance / scale_x
            z = site["z"] + math.sin(jitter_angle) * jitter_distance / scale_z
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

            # Multi-shell targets are typical of recursive pours. Their height
            # can jump from one grain to the next inside a cache cell, so the
            # first hit must be exact rather than a tangent-plane estimate.
            initial_projection = project_surface(
                x, z, force_exact=composite_target
            )
            if initial_projection is None:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=3)
                continue

            radius = grain_size * rng.uniform(
                1.0 - size_variation, 1.0 + size_variation
            )
            yaw = rng.uniform(0.0, math.pi * 2.0)
            stretch = rng.uniform(0.78, 1.28)
            grain_normal = _randomized_normal(
                initial_projection["surface_normal"], rotation_variance, rng
            )
            axes = _oriented_grain_axes(grain_normal, yaw)
            radii = (
                radius * stretch * axis_extents[0],
                radius * flattening * axis_extents[1],
                radius / stretch * axis_extents[2],
            )
            vertical_support = grain_mesh_support_radius(
                gravity_up, axes, radii
            )
            placement = evaluate_drop(
                x,
                z,
                axes,
                radii,
                vertical_support,
                projection=initial_projection,
            )
            if placement is None:
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=5)
                continue

            moved_during_settling = False
            if settling_iterations > 0 and not placement["stable"]:
                for settle_index in range(settling_iterations):
                    search_distance = (
                        grain_size
                        * settling_radius
                        * (0.62 ** settle_index)
                    )
                    if search_distance < grain_size * 0.04:
                        break
                    phase = rng.uniform(0.0, math.pi * 2.0)
                    best = placement
                    best_energy = (
                        best["center_elevation"]
                        + balance_penalty(best["x"], best["z"])
                    )
                    for direction_index in range(8):
                        angle = phase + math.pi * 2.0 * direction_index / 8.0
                        trial = evaluate_drop(
                            placement["x"]
                            + math.cos(angle) * search_distance / scale_x,
                            placement["z"]
                            + math.sin(angle) * search_distance / scale_z,
                            axes,
                            radii,
                            vertical_support,
                        )
                        if trial is None:
                            continue
                        trial_energy = (
                            trial["center_elevation"]
                            + balance_penalty(trial["x"], trial["z"])
                        )
                        height_delta = trial_energy - best_energy
                        better_height = height_delta < -grain_size * 0.025
                        similar_height = abs(height_delta) <= grain_size * 0.025
                        better_support = (
                            trial["stable"]
                            and not best["stable"]
                            and height_delta <= grain_size * 0.10
                        ) or (
                            similar_height
                            and trial["neighbor_contacts"]
                            > best["neighbor_contacts"]
                        )
                        if better_height or better_support:
                            best = trial
                            best_energy = trial_energy
                    if best is placement:
                        continue
                    placement = best
                    moved_during_settling = True
                    if placement["stable"]:
                        break

            # Above the terrain, a single contact is a balance point/column,
            # not a settled grain. Require a multi-neighbor pocket after relax.
            if settling_iterations > 0 and not placement["stable"]:
                consecutive_failures += 1
                # A site that is unstable now may become a valid pocket after
                # surrounding grains arrive, so retire it conservatively.
                _mark_frontier_failure(frontier, site_index, retire_after=30)
                continue

            if moved_during_settling:
                # Re-align the final grain to the normal where it actually
                # settled, then keep that refinement only if support remains
                # valid with the updated orientation.
                refined_normal = _randomized_normal(
                    placement["surface_normal"], rotation_variance, rng
                )
                refined_axes = _oriented_grain_axes(refined_normal, yaw)
                refined_vertical = grain_mesh_support_radius(
                    gravity_up, refined_axes, radii
                )
                refined_placement = evaluate_drop(
                    placement["x"],
                    placement["z"],
                    refined_axes,
                    radii,
                    refined_vertical,
                )
                if refined_placement is not None and (
                    settling_iterations == 0 or refined_placement["stable"]
                ):
                    grain_normal = refined_normal
                    axes = refined_axes
                    vertical_support = refined_vertical
                    placement = refined_placement

            # Cached tangent planes are useful search hints but are not trusted
            # as final terrain contacts. Re-raycast the accepted position,
            # realign to its exact normal, and recompute every support constraint
            # before adding geometry. This prevents discontinuous grain shells
            # from donating their height to nearby gaps or lower surfaces.
            exact_projection = project_surface(
                placement["x"], placement["z"], force_exact=True
            )
            if exact_projection is None:
                placement_stats["exact_rejects"] += 1
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=12)
                continue
            exact_normal = _randomized_normal(
                exact_projection["surface_normal"], rotation_variance, rng
            )
            exact_axes = _oriented_grain_axes(exact_normal, yaw)
            exact_vertical = grain_mesh_support_radius(
                gravity_up, exact_axes, radii
            )
            exact_placement = evaluate_drop(
                placement["x"],
                placement["z"],
                exact_axes,
                radii,
                exact_vertical,
                projection=exact_projection,
            )
            if exact_placement is None or (
                settling_iterations > 0 and not exact_placement["stable"]
            ):
                placement_stats["exact_rejects"] += 1
                consecutive_failures += 1
                _mark_frontier_failure(frontier, site_index, retire_after=12)
                continue
            grain_normal = exact_normal
            axes = exact_axes
            vertical_support = exact_vertical
            placement = exact_placement
            if moved_during_settling:
                placement_stats["settled"] += 1
            grains.append(
                (
                    placement["center"],
                    grain_normal,
                    radius,
                    flattening,
                    yaw,
                    stretch,
                )
            )
            spatial_hash.setdefault(placement["cell"], []).append(
                {
                    "u": placement["u"],
                    "v": placement["v"],
                    "center_elevation": placement["center_elevation"],
                    "vertical_support": vertical_support,
                    "axes": axes,
                    "radii": radii,
                }
            )
            sector_populations[placement["sector"]] += 1
            site["uses"] += 1
            if site["uses"] >= site["max_uses"]:
                frontier[site_index] = frontier[-1]
                frontier.pop()
            else:
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
                    "Settling grains: {:,} of {:,} | {:,} moved | {:,} rays".format(
                        len(grains),
                        count,
                        placement_stats["settled"],
                        placement_stats["raycasts"],
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
            "Built {:,} grains on {} with seed {}; {:,} settled, {:,} raycasts "
            "({:,} forced), {:,} cached projections, {:,} exact-contact rejects, "
            "{:,} steep-facet rejects"
        ).format(
            len(grains),
            target_transform,
            seed,
            placement_stats["settled"],
            placement_stats["raycasts"],
            placement_stats["forced_raycasts"],
            placement_stats["cache_hits"],
            placement_stats["exact_rejects"],
            placement_stats["steep_rejects"],
        )
        if composite_target:
            message += "; composite target detected ({:,} shells)".format(
                target_shell_count
            )
        message += "; occupied radial extent {:.0f}%".format(
            effective_radius * 100.0
        )
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


def _set_radial_density_strength(value):
    if cmds.objExists(SIZE_CTRL + ".radialDensityStrength"):
        cmds.setAttr(
            SIZE_CTRL + ".radialDensityStrength",
            max(0.0, min(float(value), 1.0)),
        )


def _set_radial_density_fade_start(value):
    if cmds.objExists(SIZE_CTRL + ".radialDensityFadeStart"):
        cmds.setAttr(
            SIZE_CTRL + ".radialDensityFadeStart",
            max(0.0, min(float(value), 0.95)),
        )


def _set_radial_density_fade_shape(value):
    if cmds.objExists(SIZE_CTRL + ".radialDensityFadeShape"):
        cmds.setAttr(
            SIZE_CTRL + ".radialDensityFadeShape",
            max(0.25, min(float(value), 4.0)),
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


def _set_settling_iterations(value):
    if cmds.objExists(SIZE_CTRL + ".settlingIterations"):
        cmds.setAttr(
            SIZE_CTRL + ".settlingIterations",
            max(0, min(int(value), 20)),
        )


def _set_settling_radius(value):
    if cmds.objExists(SIZE_CTRL + ".settlingRadius"):
        cmds.setAttr(
            SIZE_CTRL + ".settlingRadius",
            max(0.0, min(float(value), 4.0)),
        )


def _set_max_support_slope(value):
    if cmds.objExists(SIZE_CTRL + ".maxSupportSlope"):
        cmds.setAttr(
            SIZE_CTRL + ".maxSupportSlope",
            max(0.0, min(float(value), 89.0)),
        )


def _set_spill_balance(value):
    if cmds.objExists(SIZE_CTRL + ".spillBalance"):
        cmds.setAttr(
            SIZE_CTRL + ".spillBalance",
            max(0.0, min(float(value), 1.0)),
        )


def _set_world_gravity(value):
    if cmds.objExists(SIZE_CTRL + ".useWorldGravity"):
        cmds.setAttr(SIZE_CTRL + ".useWorldGravity", bool(value))


def _set_proposal_batch_size(value):
    if cmds.objExists(SIZE_CTRL + ".proposalBatchSize"):
        cmds.setAttr(
            SIZE_CTRL + ".proposalBatchSize",
            max(1, min(int(value), 16)),
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
    radial_density_strength = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityStrength")
    )
    radial_density_fade_start = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityFadeStart")
    )
    radial_density_fade_shape = float(
        cmds.getAttr(SIZE_CTRL + ".radialDensityFadeShape")
    )
    seed = int(cmds.getAttr(SIZE_CTRL + ".seed"))
    auto_increment_seed = bool(
        cmds.getAttr(SIZE_CTRL + ".autoIncrementSeed")
    )
    failure_limit = int(cmds.getAttr(SIZE_CTRL + ".maxFailedPlacements"))
    packing_tightness = float(cmds.getAttr(SIZE_CTRL + ".packingTightness"))
    settling_iterations = int(cmds.getAttr(SIZE_CTRL + ".settlingIterations"))
    settling_radius = float(cmds.getAttr(SIZE_CTRL + ".settlingRadius"))
    max_support_slope = float(cmds.getAttr(SIZE_CTRL + ".maxSupportSlope"))
    spill_balance = float(cmds.getAttr(SIZE_CTRL + ".spillBalance"))
    use_world_gravity = bool(cmds.getAttr(SIZE_CTRL + ".useWorldGravity"))
    proposal_batch_size = int(cmds.getAttr(SIZE_CTRL + ".proposalBatchSize"))
    use_projection_cache = bool(cmds.getAttr(SIZE_CTRL + ".useProjectionCache"))
    high_detail = bool(cmds.getAttr(SIZE_CTRL + ".highDetailGrains"))
    soften_edges = bool(cmds.getAttr(SIZE_CTRL + ".softEdges"))
    show_progress = bool(cmds.getAttr(SIZE_CTRL + ".showProgress"))
    slider_max = max(grain_size * 4.0, 0.1)

    window = cmds.window(
        CONTROL_WINDOW,
        title="Sand Heap Controls",
        sizeable=True,
        widthHeight=(420, 920),
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
    cmds.floatSliderGrp(
        label="Density Strength",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1.0,
        value=radial_density_strength,
        step=0.01,
        precision=2,
        dragCommand=_set_radial_density_strength,
        changeCommand=_set_radial_density_strength,
    )
    cmds.floatSliderGrp(
        label="Density Fade Start",
        field=True,
        minValue=0.0,
        maxValue=0.95,
        fieldMinValue=0.0,
        fieldMaxValue=0.95,
        value=radial_density_fade_start,
        step=0.01,
        precision=2,
        dragCommand=_set_radial_density_fade_start,
        changeCommand=_set_radial_density_fade_start,
    )
    cmds.floatSliderGrp(
        label="Density Fade Shape",
        field=True,
        minValue=0.25,
        maxValue=4.0,
        fieldMinValue=0.25,
        fieldMaxValue=4.0,
        value=radial_density_fade_shape,
        step=0.05,
        precision=2,
        dragCommand=_set_radial_density_fade_shape,
        changeCommand=_set_radial_density_fade_shape,
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
    cmds.intSliderGrp(
        label="Settling Passes",
        field=True,
        minValue=0,
        maxValue=20,
        fieldMinValue=0,
        fieldMaxValue=20,
        value=settling_iterations,
        step=1,
        dragCommand=_set_settling_iterations,
        changeCommand=_set_settling_iterations,
    )
    cmds.floatSliderGrp(
        label="Lateral Settling",
        field=True,
        minValue=0.0,
        maxValue=4.0,
        fieldMinValue=0.0,
        fieldMaxValue=4.0,
        value=settling_radius,
        step=0.05,
        precision=2,
        dragCommand=_set_settling_radius,
        changeCommand=_set_settling_radius,
    )
    cmds.floatSliderGrp(
        label="Max Support Slope",
        field=True,
        minValue=0.0,
        maxValue=89.0,
        fieldMinValue=0.0,
        fieldMaxValue=89.0,
        value=max_support_slope,
        step=1.0,
        precision=1,
        dragCommand=_set_max_support_slope,
        changeCommand=_set_max_support_slope,
    )
    cmds.floatSliderGrp(
        label="Spill Balance",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=1.0,
        value=spill_balance,
        step=0.05,
        precision=2,
        dragCommand=_set_spill_balance,
        changeCommand=_set_spill_balance,
    )
    cmds.intSliderGrp(
        label="Proposal Batch",
        field=True,
        minValue=1,
        maxValue=16,
        fieldMinValue=1,
        fieldMaxValue=16,
        value=proposal_batch_size,
        step=1,
        dragCommand=_set_proposal_batch_size,
        changeCommand=_set_proposal_batch_size,
    )
    cmds.checkBox(
        label="Settle using world gravity",
        value=use_world_gravity,
        changeCommand=_set_world_gravity,
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
        label="Spill balance resists one-sided avalanches on ridges and hills.",
        align="left",
    )
    cmds.showWindow(window)


cmds.undoInfo(openChunk=True, chunkName="Build Sand Heap")
try:
    build_sand_heap()
finally:
    cmds.undoInfo(closeChunk=True)

_show_control_window()
