#!/bin/bash
# Script to generate Let's Encrypt SSL certificates for production
# Run this script on your server after setting up your domain

set -e

# Configuration
DOMAIN=${DOMAIN:-yourdomain.com}
EMAIL=${LETSENCRYPT_EMAIL:-admin@yourdomain.com}

# Install certbot if not installed
if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    apt-get update
    apt-get install -y certbot
fi

# Stop nginx temporarily for certificate generation
echo "Stopping nginx for certificate generation..."
docker-compose stop nginx

# Generate certificate
echo "Generating Let's Encrypt certificate for $DOMAIN..."
certbot certonly --standalone \
    --email $EMAIL \
    --agree-tos \
    --no-eff-email \
    --domain $DOMAIN \
    --domain www.$DOMAIN

# Copy certificates to docker/ssl directory
echo "Copying certificates to docker/ssl..."
cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem docker/ssl/crm.crt
cp /etc/letsencrypt/live/$DOMAIN/privkey.pem docker/ssl/crm.key

# Set proper permissions
chmod 644 docker/ssl/crm.crt
chmod 600 docker/ssl/crm.key

# Restart services
echo "Restarting services..."
docker-compose up -d nginx

echo "SSL certificates generated successfully!"
echo "Certificate: docker/ssl/crm.crt"
echo "Private key: docker/ssl/crm.key"
echo ""
echo "Don't forget to:"
echo "1. Set up automatic renewal: crontab -e"
echo "   Add: 0 12 * * * /usr/bin/certbot renew --quiet --post-hook 'docker-compose restart nginx'"
echo "2. Update your .env file with production HTTPS settings"