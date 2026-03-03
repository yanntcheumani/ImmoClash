import { CircleMarker, MapContainer, TileLayer } from "react-leaflet";

interface Props {
  lat: number | null;
  lng: number | null;
}

const PARIS_CENTER: [number, number] = [48.8566, 2.3522];

export function MiniMap({ lat, lng }: Props) {
  const hasCoords = lat !== null && lng !== null;
  const center: [number, number] = hasCoords ? [lat as number, lng as number] : PARIS_CENTER;

  return (
    <div className="map-wrapper">
      <MapContainer center={center} zoom={hasCoords ? 13 : 5} className="mini-map" scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {hasCoords && <CircleMarker center={center} radius={10} pathOptions={{ color: "#2d6df9", fillColor: "#2d6df9", fillOpacity: 0.7 }} />}
      </MapContainer>
    </div>
  );
}
