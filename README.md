# Maya Sand Heap Generator

A single Python script for Autodesk Maya that generates a configurable pile of
low-poly sand grains on a polygon mesh.

The generated pile is driven by two editable NURBS curves:

- `sandHeap_size_CTRL` controls the footprint, position, scale, and orientation.
- `sandHeap_falloff_CTRL` is a side-view graph controlling the heap profile.

Grains are ray-projected onto the target mesh and deposited bottom-up. Cached
terrain projections and an active-site spatial hash raise each new grain to the
lowest position supported by the terrain or a nearby grain, while the falloff
profile acts as the pile's upper boundary. The result is combined into one
polygon object for viewport performance, with each grain remaining a disconnected
mesh shell.

## Usage

1. Open Maya and select the polygon mesh that should support the sand.
2. Open the Python tab of Maya's Script Editor.
3. Copy and run the complete contents of [`maya_sand_heap.py`](maya_sand_heap.py).
4. Move, rotate, scale, or edit the CVs of `sandHeap_size_CTRL`.
5. Edit `sandHeap_falloff_CTRL` to change the side-view profile.
6. Adjust grain size, size/rotation variance, and performance options in the
   generated control window, or edit the custom Channel Box attributes on
   `sandHeap_size_CTRL`.
7. Press **Rebuild Sand Heap**, or run the script again, to rebuild the pile.

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
| `rotationVariance` | Maximum random tilt away from the target surface normal |
| `radialDensityFalloff` | Bias placement density from the center toward the edge |
| `falloffPower` | Additional shaping applied to the falloff curve |
| `seed` | Random seed used by the next rebuild |
| `autoIncrementSeed` | Advance the seed after each successful rebuild |
| `maxFailedPlacements` | Consecutive failed placements before an exhausted build stops |
| `useProjectionCache` | Reuse terrain height and normal within each grain-sized cell |
| `packingTightness` | Contact spacing; values below 1 add slight visual overlap |
| `highDetailGrains` | Use 20-face grains instead of the faster 8-face grains |
| `softEdges` | Run Maya's soft-edge operation on the combined output |
| `showProgress` | Show interruptible progress windows for each build phase |

The size controller's local `-Y` axis is the projection direction. Rotate the
controller when working on sloped or unusually oriented surfaces. Scaling the
size controller also scales the generated grains. For non-uniform X/Z scaling,
the script uses the geometric mean of those two scale factors for grain size.
Grain orientation begins at the target normal, receives up to the configured
rotation variance, and then gets a random yaw.

Radial density falloff is independent of the profile curve. `0` distributes
placement attempts uniformly across viable sites; larger values increasingly
favor the center. The profile curve still defines the maximum supported height.

Seed auto-increment is enabled by default. A successful rebuild uses the seed
shown in the control window and then advances it for the next run. Disable the
option to reproduce a particular arrangement. Cancelled or failed rebuilds do
not advance the seed.

## Performance

The script converts both controller curves into constant-time lookup tables,
generates a Poisson-disk active set of viable deposition sites, and caches mesh
raycasts per grain-sized terrain cell. The blue-noise site spacing avoids visible
rows and columns while retaining fast frontier deposition. Cached hits use the
sampled tangent plane, so nearby grains still follow local slope instead of
sharing a flat elevation.
The standard defaults use softened 20-face grains. Disable **High-detail grains**
and **Soften grain edges** for a faster preview. Disable terrain caching only when
the target has features smaller than a grain that need exact per-grain projection.

Build phases use separate interruptible Maya progress windows. Escape cancels a
rebuild before the existing `sandHeap_GEO` is replaced.

## Notes

- The target must be a polygon mesh.
- Rebuilding replaces `sandHeap_GEO`.
- To maintain several independent heaps in one scene, give each copy of the
  script a different `PREFIX` value.
- High grain counts create proportionally heavier geometry.

## License

MIT
