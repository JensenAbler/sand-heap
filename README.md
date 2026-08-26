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
| `grainCount` | Requested grains in the original heap; outer-halo grains are additive |
| `heapHeight` | Maximum height at the center |
| `grainSize` | Average grain radius |
| `grainSizeVariation` | Random size variation |
| `grainFlattening` | Vertical grain scale |
| `rotationVariance` | Maximum random tilt away from the target surface normal |
| `grainIrregularityMin` | Smooth end of the per-grain irregularity range |
| `grainIrregularity` | Irregular end of the range, shown as **Irregularity Max** |
| `grainIrregularityBias` | Favor the smooth end (-1), uniform sampling (0), or the irregular end (+1) |
| `radialDensityFalloff` | Reduce supported grain capacity from the center toward the edge |
| `outerHaloOnly` | Generate only the halo and skip the original interior heap |
| `outerHaloOffset` | Move the halo start inward (negative) or outward (positive) in footprint radii |
| `outerHaloExtent` | Set the halo's radial width in footprint radii |
| `outerHaloDensity` | Unbounded surface-site density at the inner edge of the outer halo |
| `outerHaloFalloff` | Shape how quickly halo occupancy fades toward its outer edge |
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
| `grainCageSubdivisions` | Cage subdivision levels (0-3) adding finer displacement octaves |
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

Each grain samples its own amplitude between **Irregularity Min** and
**Irregularity Max**, then receives a unique randomly displaced cage instead of
a shared regular polyhedron. **Irregularity Bias** shapes that sampling: `-1`
strongly favors the smoother end, `0` distributes grains uniformly through the
range, and `+1` strongly favors the irregular end. Equal minimum and maximum
values restore the previous fixed-amplitude behavior; a value of `0` restores
the shared regular cage. Placement contacts use each grain's actual displaced
vertices, so irregular grains still pack without gaps.

**Cage Subdivisions** adds fractal detail octaves. The coarse cage receives
the full irregularity amplitude; each subdivision level midpoint-subdivides
the cage and displaces only its new vertices at half the previous amplitude,
so grains gain coarse lumps first and progressively finer crags on top rather
than uniform per-vertex noise. Every level multiplies the face count by four
(a high-detail grain has 20, 80, 320, or 1280 faces at levels 0-3) and slows
the placement pass, since contact offsets max over the denser cage. Levels 2
and 3 are best reserved for close-up hero grains or final rebuilds.

Radial density falloff is independent of the profile curve. `0` leaves every
viable site's carrying capacity unchanged; larger values progressively reduce
how many grains edge sites can accept. Consumed sites leave the active frontier,
so a fixed requested count can no longer refill a thinned edge. It has no hard
maximum. Extreme values can intentionally exhaust supported capacity before the
requested grain count is reached.

The outer halo is additive and disabled when **Halo Extent** is `0`. Enabling it
does not subtract from `grainCount`: the normal heap is completed first, then a
separately seeded blue-noise annulus is scattered. **Halo Offset** moves its
starting radius relative to the footprint edge: `0` starts at normalized radius
`1`, positive values leave an outward gap, and negative values pull the additive
surface layer inward over the original footprint. **Halo Extent** sets the width
from that starting radius. Halo grains are restricted to direct terrain contact
and cannot stack. **Halo Density** sets occupancy at the annulus's inner edge,
while **Halo Falloff** shapes its fade to zero across the requested width. Halo
density has no hard maximum. Values up to `1` thin a base blue-noise site set;
values above `1` create proportionally more candidates with closer spacing.
Halo grains remain terrain-bound and may overlap other halo grains at high
density, allowing the control to reach complete visible ground coverage without
turning the halo into a stacked second layer. They still respect collisions with
the original interior heap.
Enable **Generate halo only** to skip the interior site-generation and deposition
phases entirely. In that mode `grainCount`, the height-profile curve, and radial
density falloff do not contribute geometry; the output contains only the
terrain-contact halo grains selected by the four halo controls.

Every generated `sandHeap_GEO` stores a human-readable build sheet in its Maya
**Notes** attribute. It records the seed actually used, all generator parameters,
the target and result counts, a formatted size-controller world matrix, and
numbered CV coordinates for both controller curves.

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

## Creasing a baked heap for subdivision

Smooth subdivision averages the grains' craggy silhouettes into blobs unless
the subdivider is told which features to keep. After a build, run
[`maya_crease_grains.py`](maya_crease_grains.py) to crease the baked heap's
edges. Its default `angle` mode maps each edge's dihedral angle onto a
semi-sharp crease weight, so strong crags stay crisp for the first
subdivision levels while shallow facets round off - angular but slightly
worn, like real grains. A `uniform` mode creases every edge equally instead.
The viewport smooth preview (press 3) honors the creases, as do polySmooth
with the OpenSubdiv method and render-time subdivision. It runs
on the first selected mesh or on `sandHeap_GEO` when nothing is selected, and
rebuilding the heap replaces the output mesh, so re-run the crease script
after each rebuild.

## Performance

The script converts both controller curves into constant-time lookup tables,
generates a Poisson-disk active set of viable deposition sites, and caches mesh
raycasts per grain-sized terrain cell. The blue-noise site spacing avoids visible
rows and columns while retaining fast frontier deposition. Cache reuse is limited
to nearby samples, misses are never shared, and every accepted grain receives a
final exact raycast before geometry is created. Targets with many disconnected
shells—such as a base mesh combined with previous pours—also use exact initial
hits. Cached tangent planes therefore remain search accelerators rather than
authoritative final contacts. Terrain and vertical contact offsets use the
rendered polyhedron's actual vertices instead of a larger smooth-ellipsoid
approximation, reducing the small visible gaps that approximation could leave.
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
