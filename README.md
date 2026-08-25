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
| `radialDensityStrength` | Amount of thinning at the occupied pile edge (`0`–`1`) |
| `radialDensityFadeStart` | Normalized occupied radius where thinning begins |
| `radialDensityFadeShape` | Curvature of the smooth density transition |
| `falloffPower` | Additional shaping applied to the falloff curve |
| `seed` | Random seed used by the next rebuild |
| `autoIncrementSeed` | Advance the seed after each successful rebuild |
| `maxFailedPlacements` | Consecutive failed placements before an exhausted build stops |
| `useProjectionCache` | Reuse terrain height and normal within each grain-sized cell |
| `packingTightness` | Contact spacing; values below 1 add slight visual overlap |
| `settlingIterations` | Number of lateral drop-and-relax search passes |
| `settlingRadius` | Initial lateral search distance in grain-size units |
| `maxSupportSlope` | Steepest target facet that may support a grain |
| `spillBalance` | Resist one-sided avalanches by penalizing overfilled radial sectors |
| `useWorldGravity` | Settle vertically in world space instead of along the controller normal |
| `proposalBatchSize` | Number of randomized active sites compared per placement |
| `highDetailGrains` | Use 20-face grains instead of the faster 8-face grains |
| `softEdges` | Run Maya's soft-edge operation on the combined output |
| `showProgress` | Show interruptible progress windows for each build phase |

The size controller's local `-Y` axis remains the mesh-projection direction, so
it can be aimed at sloped or unusually oriented surfaces. Settling uses world
gravity by default, independently of that projection direction. Disable **Settle
using world gravity** to use the controller normal for both operations. Scaling
the size controller also scales the generated grains. For non-uniform X/Z
scaling, the script uses the geometric mean of those two scale factors for grain
size. Grain orientation begins at the target normal, receives up to the
configured rotation variance, and then gets a random yaw.

Radial density is independent of the profile curve. **Density Strength** blends
from uniform occupancy at `0` to the full configured fade at `1`. **Fade Start**
sets the dense core radius, and **Fade Shape** adjusts the curvature between that
core and the occupied edge. The transition uses a bounded smoothstep field rather
than an exponent that can collapse into a binary radius.

The script divides the footprint into narrow annular bands and assigns each band
a quota from its area, profile capacity, and smooth density weight. Quotas are
allocated one physical layer at a time, giving sparse outer bands wider blue-noise
gaps without turning them into separate stacked clusters. The controller is the
maximum possible footprint: when the requested grain count cannot fill it, the
occupied radial extent contracts so the result remains one central pile. The old
`radialDensityFalloff` attribute is migrated once and hidden for compatibility.

Packing uses a drop-and-relax approximation. Ground grains stop on the sampled
terrain; raised grains search laterally for a lower position and must finish in
a pocket supported by at least two neighboring grains. Unsupported single-contact
balance points are rejected rather than becoming vertical columns. Set Settling
Passes to `0` for the older, faster first-support behavior.

**Max Support Slope** defaults to `60` degrees. Steeper hits are usually grain
sidewalls or near-vertical facets rather than stable resting surfaces. Rejecting
them also prevents the normal-based contact offset from becoming excessively
large as a facet approaches vertical. Raise the limit for unusually steep target
terrain, or lower it when recursive pours still catch too many grain sidewalls.

World-gravity settling removes controller-tilt bias on hills. **Spill Balance**
then discourages a small random advantage from turning into a completely
one-sided avalanche; `0` disables that correction and `1` applies its strongest
sector balancing. Proposal Batch compares several randomized deposition sites
before each drop, reducing sequential first-mover sensitivity at a modest cost.

Seed auto-increment is enabled by default. A successful rebuild uses the seed
shown in the control window and then advances it for the next run. Disable the
option to reproduce a particular arrangement. Cancelled or failed rebuilds do
not advance the seed.

## Performance

The script converts both controller curves into constant-time lookup tables,
generates a Poisson-disk active set of viable deposition sites, and caches mesh
raycasts per grain-sized terrain cell. The blue-noise site spacing avoids visible
rows and columns while retaining fast frontier deposition. Global dart sampling
covers the controller uniformly without independent growth seeds that can become
small pile islands. Cache reuse is limited to nearby samples, misses are never
shared, and every accepted grain receives a final exact raycast before geometry
is created. Targets with many disconnected shells—such as a base mesh combined
with previous pours—also use exact initial hits. Cached tangent planes therefore
remain search accelerators rather than authoritative final contacts. Terrain and
vertical contact offsets use the rendered polyhedron's actual vertices instead
of a larger smooth-ellipsoid approximation, reducing the small visible gaps that
approximation could leave.
The standard defaults use softened 20-face grains. Disable **High-detail grains**
and **Soften grain edges** for a faster preview. Disable terrain caching only when
the target has features smaller than a grain that need exact per-grain projection.
Reducing Settling Passes is the largest speed/quality tradeoff for dense heaps.

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
