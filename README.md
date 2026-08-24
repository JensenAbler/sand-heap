# Maya Sand Heap Generator

A single Python script for Autodesk Maya that generates a configurable pile of
low-poly sand grains on a polygon mesh.

The generated pile is driven by two editable NURBS curves:

- `sandHeap_size_CTRL` controls the footprint, position, scale, and orientation.
- `sandHeap_falloff_CTRL` is a side-view graph controlling the heap profile.

Grains are ray-projected onto the target mesh, follow its surface normals, and
are combined into one polygon object for viewport performance. Each grain remains
a disconnected mesh shell.

## Usage

1. Open Maya and select the polygon mesh that should support the sand.
2. Open the Python tab of Maya's Script Editor.
3. Copy and run the complete contents of [`maya_sand_heap.py`](maya_sand_heap.py).
4. Move, rotate, scale, or edit the CVs of `sandHeap_size_CTRL`.
5. Edit `sandHeap_falloff_CTRL` to change the side-view profile.
6. Adjust the custom Channel Box attributes on `sandHeap_size_CTRL`.
7. Run the script again to rebuild the pile.

After the first run, the target mesh is remembered through a message connection,
so it does not need to remain selected.

## Parameters

| Attribute | Purpose |
| --- | --- |
| `grainCount` | Number of grains to generate |
| `heapHeight` | Maximum height at the center |
| `grainSize` | Average grain radius |
| `grainSizeVariation` | Random size variation |
| `grainFlattening` | Vertical grain scale |
| `falloffPower` | Additional shaping applied to the falloff curve |
| `seed` | Random seed for repeatable results |

The size controller's local `-Y` axis is the projection direction. Rotate the
controller when working on sloped or unusually oriented surfaces.

## Notes

- The target must be a polygon mesh.
- Rebuilding replaces `sandHeap_GEO`.
- To maintain several independent heaps in one scene, give each copy of the
  script a different `PREFIX` value.
- High grain counts create proportionally heavier geometry.

## License

MIT

