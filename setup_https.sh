#!/bin/bash
# HTTPS Setup Script for Omnichannel CRM
# This script helps you configure HTTPS with your own domain and certificates

set -e

echo "🔐 HTTPS Setup for Omnichannel CRM"
echo "=================================="

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "📄 Creating .env file from template..."
    cp env.example .env
    echo "✅ Created .env file. Please edit it with your settings."
fi

# Ask for domain
read -p "Enter your domain name (e.g., crm.yourcompany.com): " DOMAIN
if [ -z "$DOMAIN" ]; then
    echo "❌ Domain is required"
    exit 1
fi

# Ask about SSL certificates
echo ""
echo "SSL Certificate Options:"
echo "1) Generate self-signed certificates (for development/testing)"
echo "2) Use existing certificates (for production)"
read -p "Choose option [1/2]: " SSL_OPTION

case $SSL_OPTION in
    1)
        echo "🔧 Generating self-signed certificates..."
        export DOMAIN=$DOMAIN
        cd docker/ssl
        python generate_certs.py
        cd ../..

        # Update .env
        sed -i.bak "s/DOMAIN=.*/DOMAIN=$DOMAIN/" .env
        sed -i.bak "s/CUSTOM_DOMAIN=.*/CUSTOM_DOMAIN=$DOMAIN/" .env
        sed -i.bak "s/SSL_CERT=.*/SSL_CERT=crm.crt/" .env
        sed -i.bak "s/SSL_KEY=.*/SSL_KEY=crm.key/" .env
        ;;

    2)
        echo "📂 Please provide paths to your SSL certificates:"
        read -p "Certificate file path (.crt/.pem): " CERT_PATH
        read -p "Private key file path (.key): " KEY_PATH

        if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
            echo "❌ Certificate files not found"
            exit 1
        fi

        # Copy certificates to docker/ssl
        cp "$CERT_PATH" docker/ssl/custom.crt
        cp "$KEY_PATH" docker/ssl/custom.key

        # Update .env
        sed -i.bak "s/DOMAIN=.*/DOMAIN=$DOMAIN/" .env
        sed -i.bak "s/CUSTOM_DOMAIN=.*/CUSTOM_DOMAIN=$DOMAIN/" .env
        sed -i.bak "s/SSL_CERT=.*/SSL_CERT=custom.crt/" .env
        sed -i.bak "s/SSL_KEY=.*/SSL_KEY=custom.key/" .env
        sed -i.bak "s|# SSL_CERT_PATH=|SSL_CERT_PATH=./docker/ssl/custom.crt|" .env
        sed -i.bak "s|# SSL_KEY_PATH=|SSL_KEY_PATH=./docker/ssl/custom.key|" .env
        ;;

    *)
        echo "❌ Invalid option"
        exit 1
        ;;
esac

# Update ALLOWED_HOSTS
sed -i.bak "s/ALLOWED_HOSTS=.*/ALLOWED_HOSTS=localhost,127.0.0.1,$DOMAIN,www.$DOMAIN/" .env

# Update CORS
sed -i.bak "s|CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=https://$DOMAIN,https://www.$DOMAIN|" .env

echo ""
echo "✅ HTTPS configuration completed!"
echo ""
echo "📋 Next steps:"
echo "1. Update your DNS to point $DOMAIN to this server"
echo "2. Edit .env file if needed (especially SECRET_KEY and database settings)"
echo "3. Run: docker-compose up -d"
echo "4. Your CRM will be available at: https://$DOMAIN"
echo ""
echo "🔒 Security Notes:"
echo "- Make sure your SSL certificates are valid and not expired"
echo "- Consider enabling HSTS in production by setting SECURE_HSTS_* variables"
echo "- For production, set DEBUG=False and update SECRET_KEY"