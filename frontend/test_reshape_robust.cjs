const turf = require('@turf/turf');

const existingLine = turf.lineString([
    [0, 0],
    [10, 0],
    [20, 0],
    [30, 0]
]);

// Sketch line that starts near [5,0] and ends near [25,0] but NEVER crosses!
const sketchLine = turf.lineString([
    [5, 0.1],
    [15, 5],
    [25, 0.1]
]);

let intersections = turf.lineIntersect(existingLine, sketchLine).features;

// If we don't have at least 2 intersections, try snapping the endpoints of the sketch
const SNAP_TOLERANCE_KM = 20; // 500 meters

const sketchStart = turf.point(sketchLine.geometry.coordinates[0]);
const sketchEnd = turf.point(sketchLine.geometry.coordinates[sketchLine.geometry.coordinates.length - 1]);

const snapStart = turf.nearestPointOnLine(existingLine, sketchStart);
if (snapStart.properties.dist <= SNAP_TOLERANCE_KM) {
    intersections.push(turf.point(snapStart.geometry.coordinates));
    // Add this point to the start of the sketch so it actually touches
    sketchLine.geometry.coordinates.unshift(snapStart.geometry.coordinates);
}

const snapEnd = turf.nearestPointOnLine(existingLine, sketchEnd);
if (snapEnd.properties.dist <= SNAP_TOLERANCE_KM) {
    intersections.push(turf.point(snapEnd.geometry.coordinates));
    // Add this point to the end of the sketch so it actually touches
    sketchLine.geometry.coordinates.push(snapEnd.geometry.coordinates);
}

console.log("Total intersection/snap points:", intersections.length);

if (intersections.length >= 2) {
    let intersectionPoints = intersections.map(f => {
        return {
            point: f,
            distOnExisting: turf.length(turf.lineSlice(turf.point(existingLine.geometry.coordinates[0]), f, existingLine))
        };
    });
    
    intersectionPoints.sort((a, b) => a.distOnExisting - b.distOnExisting);
    const firstInt = intersectionPoints[0];
    const lastInt = intersectionPoints[intersectionPoints.length - 1];
    
    const part1 = turf.lineSlice(turf.point(existingLine.geometry.coordinates[0]), firstInt.point, existingLine);
    const part3 = turf.lineSlice(lastInt.point, turf.point(existingLine.geometry.coordinates[existingLine.geometry.coordinates.length - 1]), existingLine);
    
    let reshapePart = turf.lineSlice(firstInt.point, lastInt.point, sketchLine);
    
    const part1End = part1.geometry.coordinates[part1.geometry.coordinates.length - 1];
    const reshapeStart = reshapePart.geometry.coordinates[0];
    
    if (turf.distance(turf.point(part1End), turf.point(reshapeStart)) > 0.001) {
        reshapePart.geometry.coordinates.reverse();
    }
    
    const coords1 = part1.geometry.coordinates.slice(0, -1);
    const coords2 = reshapePart.geometry.coordinates;
    const coords3 = part3.geometry.coordinates.slice(1);
    
    const newCoords = [...coords1, ...coords2, ...coords3];
    console.log("New Coords:", JSON.stringify(newCoords));
}

