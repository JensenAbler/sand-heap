"""
Paste this whole file into Maya's Python Script Editor and run it after
building a sand heap with maya_sand_heap.py.

It creases the baked heap's edges so smooth subdivision keeps the grains'
craggy silhouettes instead of averaging them into blobs. OpenSubdiv
Catmull-Clark honors crease weights in the viewport smooth preview
(enabled by this script), in polySmooth with the OpenSubdiv method, and
in render-time subdivision.

Runs on the first selected polygon mesh, or on sandHeap_GEO when nothing
is selected. Re-running replaces the previous crease values. To remove
all creasing again, run:

    cmds.polyCrease("sandHeap_GEO.e[*]", value=0.0)
"""

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

PREFIX = "sandHeap"
TARGET = PREFIX + "_GEO"

# "uniform" creases every edge with UNIFORM_WEIGHT. "angle" maps each edge's
# dihedral angle from MIN_ANGLE..MAX_ANGLE degrees onto MIN_WEIGHT..MAX_WEIGHT,
# so strong crags stay sharp while shallow facets round off. Weights of 1-3
# are semi-sharp: crisp for the first subdivision levels, then rounding, which
# reads as angular-but-worn grains. Weights of 4+ stay knife-sharp forever.
MODE = "angle"
UNIFORM_WEIGHT = 2.0
MIN_ANGLE = 10.0
MAX_ANGLE = 75.0
MIN_WEIGHT = 0.0
MAX_WEIGHT = 3.0
# Edges are grouped into weight buckets of this size so the whole mesh needs
# only a handful of polyCrease calls instead of one per edge.
WEIGHT_QUANTIZE = 0.25


def _target_mesh():
    """Return (transform, shape) for the selection or the default heap."""
    candidates = cmds.ls(selection=True, objectsOnly=True, long=True) or []
    if cmds.objExists(TARGET):
        candidates.append(TARGET)
    for node in candidates:
        if cmds.nodeType(node) == "mesh":
            parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parents:
                return parents[0], node
            continue
        shapes = cmds.listRelatives(
            node, shapes=True, noIntermediate=True, fullPath=True, type="mesh"
        ) or []
        if shapes:
            return node, shapes[0]
    raise RuntimeError(
        "Select the baked heap mesh, or build {} first.".format(TARGET)
    )


def _dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _angle_weight_buckets(shape):
    """Group edge indices by quantized dihedral-angle crease weight."""
    mesh_fn = om.MFnMesh(_dag_path(shape))
    normals = [
        mesh_fn.getPolygonNormal(face, om.MSpace.kObject)
        for face in range(mesh_fn.numPolygons)
    ]
    angle_span = max(MAX_ANGLE - MIN_ANGLE, 1.0e-6)
    buckets = {}
    edge_iter = om.MItMeshEdge(_dag_path(shape))
    while not edge_iter.isDone():
        faces = edge_iter.getConnectedFaces()
        if len(faces) == 2:
            angle = math.degrees(normals[faces[0]].angle(normals[faces[1]]))
            blend = (angle - MIN_ANGLE) / angle_span
            weight = MIN_WEIGHT + (MAX_WEIGHT - MIN_WEIGHT) * max(
                0.0, min(blend, 1.0)
            )
            weight = round(weight / WEIGHT_QUANTIZE) * WEIGHT_QUANTIZE
            if weight > 0.0:
                buckets.setdefault(weight, []).append(edge_iter.index())
        edge_iter.next()
    return buckets


def crease_sand_grains():
    transform, shape = _target_mesh()

    if MODE == "uniform":
        cmds.polyCrease(
            transform + ".e[*]", value=max(float(UNIFORM_WEIGHT), 0.0)
        )
        summary = "creased every edge at weight {}".format(UNIFORM_WEIGHT)
    else:
        # Clear first so previously creased edges that now map to zero do not
        # keep their old weight.
        cmds.polyCrease(transform + ".e[*]", value=0.0)
        buckets = _angle_weight_buckets(shape)
        creased = 0
        for weight in sorted(buckets):
            edges = buckets[weight]
            cmds.polyCrease(
                ["{}.e[{}]".format(transform, index) for index in edges],
                value=weight,
            )
            creased += len(edges)
        summary = "creased {:,} edges across {} weight buckets".format(
            creased, len(buckets)
        )

    # polyCrease leaves history nodes behind; the generated heap has no
    # meaningful history, so bake the crease values into the shape.
    cmds.delete(transform, constructionHistory=True)

    cmds.select(transform, replace=True)
    om.MGlobal.displayInfo(
        "{}: {} (press 3 to preview; polySmooth with the OpenSubdiv "
        "method or render-time subdivision keeps the creases)".format(
            transform.rsplit("|", 1)[-1], summary
        )
    )


cmds.undoInfo(openChunk=True, chunkName="Crease Sand Grains")
try:
    crease_sand_grains()
finally:
    cmds.undoInfo(closeChunk=True)
