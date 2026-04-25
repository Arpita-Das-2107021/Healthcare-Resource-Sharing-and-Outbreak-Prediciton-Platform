export type UserRole = 'admin' | 'pharmacist' | 'doctor' | 'coordinator' | 'regulator' | 'nurse' | 'technician';

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  hospital: string;
  avatar?: string;
}

export interface Employee extends User {
  department: string;
  isOnline: boolean;
  lastSeen?: string;
  phoneNumber?: string;
  specialization?: string;
}

export interface InventoryItem {
  id: string;
  name: string;
  category: string;
  abcClassification: 'A' | 'B' | 'C';
  vedClassification: 'V' | 'E' | 'D';
  currentStock: number;
  reorderLevel: number;
  maxStock: number;
  unitPrice: number;
  expiryDate: string;
  supplier: string;
  lastUpdated: string;
  hospital: string;
}

export interface SharedResource {
  id: string;
  name: string;
  type: 'drugs' | 'blood' | 'organs' | 'equipment';
  hospital: string;
  quantity: number;
  availability: 'available' | 'limited' | 'unavailable';
  isEmergency: boolean;
  region: string;
  lastUpdated: string;
}

export interface ResourceWithVisibility extends SharedResource {
  resourceTypeName?: string;
  isVisibleToOthers: boolean;
  requestCount?: number;
  image?: string;
  description?: string;
  bloodType?: string;
  expiryDate?: string;
  hospitalId?: string;      // UUID of the supplying hospital
  catalogItemId?: string;   // UUID of the catalog item
}

export interface ResourceRequest {
  id: string;
  resourceName: string;
  resourceType: 'drugs' | 'blood' | 'organs' | 'equipment';
  requestingHospital: string;
  providingHospital: string;
  quantity: number;
  urgency: 'routine' | 'urgent' | 'critical';
  status: 'pending' | 'approved' | 'in_transit' | 'delivered' | 'rejected';
  justification?: string;
  requestedAt: string;
  updatedAt: string;
}

export interface Alert {
  id: string;
  type: 'shortage' | 'expiry' | 'emergency' | 'substitution';
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  hospital: string;
  resourceId?: string;
  isRead: boolean;
  createdAt: string;
}

export type ConversationType = 'private' | 'group' | 'case';
export type MessageStatus = 'sent' | 'delivered' | 'read';
export type MessageAttachmentType = 'image' | 'file' | 'audio' | 'video';

export interface MessageAttachment {
  id: string;
  name: string;
  type: MessageAttachmentType;
  url: string;
  size: number;
  mimeType?: string;
  mediaKind?: 'image' | 'file' | 'voice' | 'video';
  processingStatus?: 'ready' | 'pending' | 'processing' | 'failed';
}

export interface Message {
  id: string;
  conversationId: string;
  sender: Employee;
  content: string;
  attachments?: MessageAttachment[];
  caseTag?: string;
  mentions?: string[]; // User IDs mentioned with @
  status: MessageStatus;
  createdAt: string;
  editedAt?: string;
  readAt?: string;
}

export interface TypingStatus {
  userId: string;
  userName: string;
  isTyping: boolean;
  timestamp: string;
}

export interface Conversation {
  id: string;
  type: ConversationType;
  name?: string; // For group chats
  participants: Employee[];
  creator?: Employee; // For group chats
  caseId?: string;
  description?: string; // For groups
  lastMessage: string;
  lastMessageAt: string;
  unreadCount: number;
  isArchived: boolean;
  isMuted: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MessageFilter {
  type?: ConversationType;
  hospital?: string;
  department?: string;
  role?: UserRole;
  caseId?: string;
}

export interface MessageSort {
  field: 'recent' | 'unread' | 'name' | 'created';
  direction: 'asc' | 'desc';
}

export interface OnlineStatus {
  userId: string;
  isOnline: boolean;
  lastSeen?: string;
}

export interface RolePermission {
  role: UserRole;
  permissions: {
    inventory: { read: boolean; write: boolean; admin: boolean };
    sharing: { read: boolean; write: boolean; admin: boolean };
    communication: { read: boolean; write: boolean; admin: boolean };
    admin: { read: boolean; write: boolean; admin: boolean };
    reports: { read: boolean; write: boolean; admin: boolean };
  };
}

export interface AuditLog {
  id: string;
  action: string;
  user: string;
  resource: string;
  details: string;
  timestamp: string;
}

export interface ForecastData {
  month: string;
  actual?: number;
  predicted: number;
  lowerBound: number;
  upperBound: number;
}

export interface KPIData {
  stockOutRate: number;
  stockOutTrend: number;
  expiringStock: number;
  expiringTrend: number;
  inventoryTurnover: number;
  turnoverTrend: number;
  pendingRequests: number;
  requestsTrend: number;
}