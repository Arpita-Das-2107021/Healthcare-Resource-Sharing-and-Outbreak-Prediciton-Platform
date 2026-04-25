import { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { type ResourceWithVisibility } from '@/types/healthcare';
import { MapPin, Clock, AlertTriangle, ShoppingCart, Eye, EyeOff } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/contexts/AuthContext';
import { Link, useNavigate } from 'react-router-dom';

const MEDICINE_IMAGE_PRIMARY = '/public/medicine.png';
const MEDICINE_IMAGE_FALLBACK = '/medicine.png';
const RESOURCE_IMAGE_FALLBACK = '/placeholder.svg';

interface ResourceCardProps {
  resource: ResourceWithVisibility;
  onRequest?: (resource: ResourceWithVisibility) => void;
  showVisibilityToggle?: boolean;
  onToggleVisibility?: (resourceId: string, visible: boolean) => void;
  onClick?: (resource: ResourceWithVisibility) => void;
  resourceRouteState?: unknown;
}

export const ResourceCard = ({ 
  resource, 
  onRequest,
  showVisibilityToggle = false,
  onToggleVisibility,
  onClick,
  resourceRouteState
}: ResourceCardProps) => {
  const [isHovered, setIsHovered] = useState(false);
  const navigate = useNavigate();
  const { user } = useAuth();
  const resourceDetailsPath = resource.id ? `/resource/${encodeURIComponent(resource.id)}` : '';

  const resolveStaticImage = (type: string): string => {
    const normalizedType = String(type || '').toLowerCase();
    if (['medicine', 'medication', 'drug', 'drugs'].includes(normalizedType)) {
      return MEDICINE_IMAGE_PRIMARY;
    }

    return RESOURCE_IMAGE_FALLBACK;
  };

  const getAvailabilityStyles = (availability: string) => {
    switch (availability) {
      case 'available': 
        return { 
          badge: 'bg-success text-success-foreground', 
          ring: 'ring-success/20',
          glow: 'shadow-success/20'
        };
      case 'limited': 
        return { 
          badge: 'bg-warning text-warning-foreground', 
          ring: 'ring-warning/20',
          glow: 'shadow-warning/20'
        };
      default: 
        return { 
          badge: 'bg-destructive text-destructive-foreground', 
          ring: 'ring-destructive/20',
          glow: 'shadow-destructive/20'
        };
    }
  };

  const getTypeIcon = (type: string) => {
    const icons: Record<string, string> = {
      blood: '🩸',
      drugs: '💊',
      organs: '🫀',
      equipment: '🏥'
    };
    return icons[type] || '📦';
  };

  const styles = getAvailabilityStyles(resource.availability);
  const displayType = String(resource.resourceTypeName || resource.type || '').trim() || 'Resource';

  const handleCardClick = (e: React.MouseEvent) => {
    // Don't trigger card click if clicking on buttons or hospital name
    if ((e.target as HTMLElement).closest('button') || 
        (e.target as HTMLElement).closest('.hospital-link')) {
      return;
    }
    
    if (onClick) {
      onClick(resource);
    } else {
      if (!resourceDetailsPath) return;
      if (resourceRouteState !== undefined) {
        navigate(resourceDetailsPath, { state: resourceRouteState });
        return;
      }
      navigate(resourceDetailsPath);
    }
  };

  const handleHospitalClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (resource.hospitalId) {
      navigate(`/hospital/${resource.hospitalId}`);
    } else {
      navigate('/hospitals');
    }
  };

  return (
    <Card 
      className={cn(
        "group overflow-hidden transition-all duration-300 cursor-pointer",
        "hover:shadow-xl hover:-translate-y-1",
        resource.isEmergency && "ring-2 ring-destructive",
        isHovered && styles.glow,
        !resource.isVisibleToOthers && showVisibilityToggle && "opacity-70"
      )}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={handleCardClick}
      data-navigation
    >
      {/* Image Section */}
      <div className="relative h-36 overflow-hidden">
        <img 
          src={resolveStaticImage(resource.type)} 
          alt={resource.name}
          className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
          onError={(event) => {
            const target = event.currentTarget;
            if (target.src.includes('/public/medicine.png')) {
              target.src = MEDICINE_IMAGE_FALLBACK;
              return;
            }

            if (!target.src.includes('/placeholder.svg')) {
              target.src = RESOURCE_IMAGE_FALLBACK;
            }
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent" />
        
        {/* Type Badge */}
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <span className="text-xl">{getTypeIcon(resource.type)}</span>
          <Badge variant="secondary" className="capitalize backdrop-blur-sm bg-background/80">
            {displayType}
          </Badge>
        </div>

        {/* Emergency Badge */}
        {resource.isEmergency && (
          <Badge variant="destructive" className="absolute top-3 right-3 animate-pulse">
            <AlertTriangle className="mr-1 h-3 w-3" />
            Emergency
          </Badge>
        )}

        {/* Availability Badge */}
        <Badge className={cn("absolute bottom-3 right-3", styles.badge)}>
          {resource.availability === 'available' ? 'Available' : 
           resource.availability === 'limited' ? 'Limited' : 'Unavailable'}
        </Badge>

        {/* Visibility indicator for pharmacist view */}
        {showVisibilityToggle && (
          <div className="absolute bottom-3 left-3">
            <Badge 
              variant={resource.isVisibleToOthers ? "default" : "outline"} 
              className={cn(
                "backdrop-blur-sm",
                resource.isVisibleToOthers ? "bg-primary/90" : "bg-background/80"
              )}
            >
              {resource.isVisibleToOthers ? (
                <><Eye className="mr-1 h-3 w-3" /> Shared</>
              ) : (
                <><EyeOff className="mr-1 h-3 w-3" /> Hidden</>
              )}
            </Badge>
          </div>
        )}
      </div>

      <CardContent className="p-4">
        {/* Title & Description */}
        <h3 className="font-semibold text-lg truncate group-hover:text-primary transition-colors">
          {resourceDetailsPath ? (
            <Link
              to={resourceDetailsPath}
              state={resourceRouteState}
              className="text-primary transition-colors hover:text-primary/90 hover:underline"
              onClick={(event) => event.stopPropagation()}
            >
              {resource.name}
            </Link>
          ) : (
            resource.name
          )}
        </h3>
        <p className="text-sm text-muted-foreground line-clamp-2 mt-1 min-h-[2.5rem]">
          {resource.description}
        </p>

        {/* Quantity */}
        <div className="mt-3 flex items-baseline gap-1">
          <span className="text-2xl font-bold text-primary">{resource.quantity}</span>
          <span className="text-sm text-muted-foreground">units available</span>
        </div>

        {/* Meta Info */}
        <div className="mt-3 space-y-1.5 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <MapPin className="h-3.5 w-3.5 shrink-0" />
            <span 
              className="truncate hospital-link cursor-pointer hover:text-primary transition-colors"
              onClick={handleHospitalClick}
            >
              {resource.hospital}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Clock className="h-3.5 w-3.5 shrink-0" />
            <span>Updated {new Date(resource.lastUpdated).toLocaleDateString()}</span>
          </div>
          {resource.expiryDate && (
            <div className="flex items-center gap-2 text-warning">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>Expires {new Date(resource.expiryDate).toLocaleDateString()}</span>
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="mt-4 flex gap-2">
          {showVisibilityToggle ? (
            <Button 
              variant={resource.isVisibleToOthers ? "outline" : "default"}
              size="sm" 
              className="flex-1 transition-all duration-200"
              onClick={() => onToggleVisibility?.(resource.id, !resource.isVisibleToOthers)}
            >
              {resource.isVisibleToOthers ? (
                <><EyeOff className="mr-2 h-4 w-4" /> Hide from Others</>
              ) : (
                <><Eye className="mr-2 h-4 w-4" /> Make Visible</>
              )}
            </Button>
          ) : (
            <Button 
              size="sm" 
              className="flex-1 transition-all duration-200 group-hover:bg-primary"
              // Disable only when this resource is from the same hospital as the current user
              disabled={!!(user && resource.hospitalId && user.hospital_id && resource.hospitalId === user.hospital_id)}
              onClick={() => onRequest?.(resource)}
            >
              <ShoppingCart className="mr-2 h-4 w-4" />
              Request Resource
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default ResourceCard;