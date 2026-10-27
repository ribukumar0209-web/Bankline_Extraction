const turf = require('@turf/turf');

const existingLine = turf.lineString([
    [0, 0],
    [10, 0],
    [20, 0],
    [30, 0]
]);

const sketchLine = turf.lineString([
    [25, -5],
    [25, 5],
    [5, 5],
    [5, -5]
]);

const intersections = turf.lineIntersect(existingLine, sketchLine);
console.log("Intersections:", intersections.features.length);

let intersectionPoints = intersections.features.map(f => {
    return {
        point: f,
        distOnExisting: turf.length(turf.lineSlice(turf.point(existingLine.geometry.coordinates[0]), f, existingLine)),
        distOnReshape: turf.length(turf.lineSlice(turf.point(sketchLine.geometry.coordinates[0]), f, sketchLine))
    };
});

intersectionPoints.sort((a, b) => a.distOnExisting - b.distOnExisting);
const firstInt = intersectionPoints[0];
const lastInt = intersectionPoints[intersectionPoints.length - 1];

console.log("First Int:", firstInt.point.geometry.coordinates);
console.log("Last Int:", lastInt.point.geometry.coordinates);

const part1 = turf.lineSlice(turf.point(existingLine.geometry.coordinates[0]), firstInt.point, existingLine);
const part3 = turf.lineSlice(lastInt.point, turf.point(existingLine.geometry.coordinates[existingLine.geometry.coordinates.length - 1]), existingLine);

console.log("Part 1 coords:", JSON.stringify(part1.geometry.coordinates));
console.log("Part 3 coords:", JSON.stringify(part3.geometry.coordinates));

let reshapePart = turf.lineSlice(firstInt.point, lastInt.point, sketchLine);
console.log("Reshape part coords:", JSON.stringify(reshapePart.geometry.coordinates));

const part1End = part1.geometry.coordinates[part1.geometry.coordinates.length - 1];
const reshapeStart = reshapePart.geometry.coordinates[0];

if (turf.distance(turf.point(part1End), turf.point(reshapeStart)) > 0.001) {
    console.log("Reversing reshape part");
    reshapePart.geometry.coordinates.reverse();
}

const coords1 = part1.geometry.coordinates.slice(0, -1);
const coords2 = reshapePart.geometry.coordinates;
const coords3 = part3.geometry.coordinates.slice(1);

const newCoords = [...coords1, ...coords2, ...coords3];
console.log("New Coords:", JSON.stringify(newCoords));
