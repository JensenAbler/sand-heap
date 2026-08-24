"""
Paste this whole file into Maya's Python Script Editor and run it.

First run: select one polygon-mesh target, then run.
Later runs: edit the two NURBS controls and/or the attributes on
`sandHeap_size_CTRL`, then run the same script again to rebuild.
"""

import math
import random

import maya.cmds as cmds
import maya.api.OpenMaya as om


# -----------------------------------------------------------------------------
# Names. Change the prefix if you want more than one independently editable heap.
# -----------------------------------------------------------------------------
PREFIX = "sandHeap"
SIZE_CTRL = PREFIX + "_size_CTRL"
PROFILE_CTRL = PREFIX + "_falloff_CTRL"
OUTPUT_GEO = PREFIX + "_GEO"
MATERIAL = PREFIX + "_MAT"
SHADING_GROUP = MATERIAL + "SG"


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
    _add_attr(SIZE_CTRL, "falloffPower", "double", 1.0, 0.05, 20.0)
    _add_attr(SIZE_CTRL, "seed", "long", 12345, 0, 2147483647)

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


def _make_material():
    if not cmds.objExists(MATERIAL):
        cmds.shadingNode("lambert", asShader=True, name=MATERIAL)
        cmds.setAttr(MATERIAL + ".color", 0.48, 0.29, 0.11, type="double3")
        cmds.setAttr(MATERIAL + ".diffuse", 0.8)
    if not cmds.objExists(SHADING_GROUP):
        cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=SHADING_GROUP)
        cmds.connectAttr(MATERIAL + ".outColor", SHADING_GROUP + ".surfaceShader", force=True)


def _build_mesh(grains):
    base_vertices, base_faces = _icosahedron()
    vertices = []
    counts = []
    connections = []

    for center, normal, radius, flattening, yaw, stretch in grains:
        normal = om.MVector(normal).normalize()
        reference = om.MVector(1, 0, 0) if abs(normal.x) < 0.9 else om.MVector(0, 0, 1)
        tangent = (reference ^ normal).normalize()
        bitangent = (normal ^ tangent).normalize()
        cosine, sine = math.cos(yaw), math.sin(yaw)
        axis_x = tangent * cosine + bitangent * sine
        axis_z = tangent * -sine + bitangent * cosine
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

    if cmds.objExists(OUTPUT_GEO):
        cmds.delete(OUTPUT_GEO)

    mesh_object = om.MFnMesh().create(vertices, counts, connections)
    shape_path = om.MDagPath.getAPathTo(mesh_object).fullPathName()
    parents = cmds.listRelatives(shape_path, parent=True, fullPath=True) or []
    if not parents:
        raise RuntimeError("Maya created the sand mesh without a transform node.")
    output = cmds.rename(parents[0], OUTPUT_GEO)
    shape = (cmds.listRelatives(output, shapes=True, fullPath=True) or [None])[0]
    if shape:
        cmds.rename(shape, OUTPUT_GEO + "Shape")
    cmds.polySoftEdge(output, angle=38, constructionHistory=False)
    _make_material()
    cmds.sets(output, edit=True, forceElement=SHADING_GROUP)
    return output


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

    footprint_points = _curve_points(SIZE_CTRL, 260)
    footprint = [(point.x, point.z) for point in footprint_points]
    if not _point_in_polygon(0.0, 0.0, footprint):
        raise RuntimeError(
            "The size controller's pivot/local origin must remain inside its curve."
        )
    min_x = min(p[0] for p in footprint)
    max_x = max(p[0] for p in footprint)
    min_z = min(p[1] for p in footprint)
    max_z = max(p[1] for p in footprint)
    profile = _profile_samples()

    count = int(cmds.getAttr(SIZE_CTRL + ".grainCount"))
    heap_height = float(cmds.getAttr(SIZE_CTRL + ".heapHeight"))
    grain_size = float(cmds.getAttr(SIZE_CTRL + ".grainSize"))
    size_variation = float(cmds.getAttr(SIZE_CTRL + ".grainSizeVariation"))
    flattening = float(cmds.getAttr(SIZE_CTRL + ".grainFlattening"))
    falloff_power = float(cmds.getAttr(SIZE_CTRL + ".falloffPower"))
    seed = int(cmds.getAttr(SIZE_CTRL + ".seed"))
    rng = random.Random(seed)

    world_matrix = om.MMatrix(cmds.getAttr(SIZE_CTRL + ".worldMatrix[0]"))
    controller_up = (om.MVector(0, 1, 0) * world_matrix).normalize()
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

    grains = []
    attempts = 0
    max_attempts = count * 120
    while len(grains) < count and attempts < max_attempts:
        attempts += 1
        x = rng.uniform(min_x, max_x)
        z = rng.uniform(min_z, max_z)
        if not _point_in_polygon(x, z, footprint):
            continue
        radius_fraction = _normalized_radius(x, z, footprint)
        relative_height = max(0.0, _profile_value(radius_fraction, profile))
        relative_height = relative_height ** falloff_power
        local_height = heap_height * relative_height
        volume_height = rng.uniform(0.0, heap_height)
        if volume_height > local_height:
            continue

        plane_point = om.MPoint(x, 0.0, z) * world_matrix
        ray_source = plane_point + controller_up * ray_offset
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
            continue

        hit_point = om.MPoint(hit[0])
        face_id = hit[2]
        try:
            surface_normal, _ = mesh_fn.getClosestNormal(hit_point, om.MSpace.kWorld)
        except (RuntimeError, TypeError):
            surface_normal = mesh_fn.getPolygonNormal(face_id, om.MSpace.kWorld)
        surface_normal = om.MVector(surface_normal).normalize()
        if surface_normal * controller_up < 0.0:
            surface_normal *= -1.0

        radius = grain_size * rng.uniform(1.0 - size_variation, 1.0 + size_variation)
        center = hit_point + surface_normal * (volume_height + radius * flattening * 0.92)
        yaw = rng.uniform(0.0, math.pi * 2.0)
        stretch = rng.uniform(0.78, 1.28)
        grains.append((center, surface_normal, radius, flattening, yaw, stretch))

    if not grains:
        raise RuntimeError(
            "No rays from the size controller hit the target. Move/rotate the size "
            "controller so its local -Y direction points through the mesh."
        )

    output = _build_mesh(grains)
    cmds.select(SIZE_CTRL, replace=True)
    message = "Built {:,} grains on {}".format(len(grains), target_transform)
    if len(grains) < count:
        message += " (requested {:,}; part of the footprint missed the target)".format(count)
    om.MGlobal.displayInfo(message)
    return output


cmds.undoInfo(openChunk=True, chunkName="Build Sand Heap")
try:
    build_sand_heap()
finally:
    cmds.undoInfo(closeChunk=True)
